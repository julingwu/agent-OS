# Agent OS (oaagent-m0) Linux 无头部署与运维指南

本文档面向系统运维工程师，详细阐述 Agent OS（自研数字集团桌面客户端底座，FastAPI + React SPA + SQLite WAL + DSH/ACP 执行引擎）在 Linux（无图形界面，Headless）环境下的生产部署、守护配置、网络接入、降级适配与灾备运维规范。

---

## 目录
1. [系统架构拓扑与关键服务](#1-系统架构拓扑与关键服务)
2. [架构硬约束：为什么必须单 Worker？](#2-架构硬约束为什么必须单-worker)
3. [Windows 到 Linux 的能力差异与降级配置](#3-windows-到-linux-的能力差异与降级配置)
4. [系统环境与基础依赖](#4-系统环境与基础依赖)
5. [前后端构建与安装步骤](#5-前后端构建与安装步骤)
6. [首次初始化与身份签发](#6-首次初始化与身份签发)
7. [Systemd 守护进程与 Gunicorn/Uvicorn 配置](#7-systemd-守护进程与-gunicornuvicorn-配置)
8. [Nginx 站点配置与反向代理](#8-nginx-站点配置与反向代理)
9. [服务验证与健康检查](#9-服务验证与健康检查)
10. [运维管理、日志与版本升级](#10-运维管理日志与版本升级)
11. [SQLite 在线热备与灾难回滚](#11-sqlite-在线热备与灾难回滚)
12. [环境变量速查字典（生产核准）](#12-环境变量速查字典生产核准)

---

## 1. 系统架构拓扑与关键服务

Agent OS 在 Linux 生产环境下的标准部署架构拓扑如下：

```
[外部终端 / 浏览器 / 企微网关]
             │
             ▼ (HTTPS / 443, HTTP / 80)
   ┌───────────────────┐
   │    Nginx 反向代理  │
   │  (SPA 静态+反代)   │
   └─────────┬─────────┘
             │
   ┌─────────┴────────────────────────────────────────┐
   │ 静态资源与 SPA 路由 (/app/)                        │ 动态 API & SSE 流式长连接 (反代)
   ▼                                                  ▼
本地磁盘: /opt/agent-os/web/dist/                     127.0.0.1:8077 (单 Worker Uvicorn/Gunicorn)
                                                      ┌──────────────────────────────────────────────┐
                                                      │ Agent OS 后端 (FastAPI, Python 3.10+)         │
                                                      │ ├─ Lifespan: UI Cron / 企微轮询守护线程        │
                                                      │ ├─ 内存表: _RUN_PROCESSES 进程句柄登记簿       │
                                                      │ └─ 数据引擎: SQLite 3 (WAL 模式)              │
                                                      └──────┬──────────────────────┬────────────────┘
                                                             │                      │
                                               SQLite 读写锁  │                      │ Popen 子进程驱动
                                                             ▼                      ▼
                                                  /opt/agent-os/data/oa.db    DSH / ACP Runner 子进程
                                                  (oa.db-wal / oa.db-shm)     (隔离工作区: /opt/agent-os/workspace)
```

- **后端底座**：FastAPI 提供 RESTful API、SSE 消息流、以及基于 SQLite 的轻量持久化。
- **前端控制台**：React 18 + Vite 构建的 SPA，在无桌面壳（Tauri）的 Linux 环境下，通过 Nginx 托管静态资源并在 `/app/` 提供纯 Web 访问。
- **数据引擎**：SQLite 3（WAL 预写日志模式，`PRAGMA journal_mode = WAL`）。
- **任务执行器**：DSH（DeepSeek Harness）ACP 子进程，负责模型交互与沙箱工具调用。

---

## 2. 架构硬约束：为什么必须单 Worker？

在 Gunicorn / Uvicorn 部署配置中，**`workers` 数量必须且只能设置为 1**。绝不能因为服务器是多核 CPU 而增加 worker 数。

以下是源码级的根本原因与证据支撑：

### 2.1 SQLite 写锁与并发冲突
- **源码依据**：`app/db.py:24-28`
  ```python
  conn = sqlite3.connect(p, timeout=30.0, check_same_thread=False)
  conn.execute("PRAGMA foreign_keys = ON")
  conn.execute("PRAGMA journal_mode = WAL")
  ```
- **机理分析**：SQLite 在 WAL 模式下支持并发读取，但**写操作仍然是全库互斥排他的（单一写锁）**。当开启多个 Worker 进程时，各 Worker 独立拥有数据库连接池。在密集任务创建、心跳上报、审计日志写入时，多进程争抢 SQLite 排他写锁极易造成锁等待超时，抛出 `sqlite3.OperationalError: database is locked`。
- **关联锁逻辑**：`app/locks.py:47-71` 中的 `acquire()` 与 `release()` 均基于 SQLite 表 `run_locks` 实现，跨进程的高频事务竞争会进一步加剧写锁碰撞。

### 2.2 进程内 Runner 句柄表与紧急熔断（Kill Switch）
- **源码依据**：`app/engine/dsh_adapter.py:50-80`
  ```python
  _RUN_PROCESSES: dict[str, subprocess.Popen] = {}
  _PROCESS_LOCK = threading.Lock()
  ...
  def stop_process(run_id: str, *, expected_pid: int | None = None) -> bool:
      with _PROCESS_LOCK:
          process = _RUN_PROCESSES.get(run_id)
          if process is None:
              if expected_pid:
                  raise RuntimeError("runner handle unavailable; refusing to terminate a stored PID")
              return False
  ```
  `app/ui/api.py:1094-1133`（`/ui/runs/{run_id}/kill` 接口）及 `app/engine/runner_acp.py:149-152`：
  ```python
  def on_started(process):
      base.register_process(run_id, process)
  ```
- **机理分析**：每个由 ACP 发起的任务子进程，其 `subprocess.Popen` 对象被注册在当前 Python 进程的**内存字典 `_RUN_PROCESSES`** 中。
  如果使用多 Worker：Worker A 启动了任务并在其内存中记录了子进程句柄。当管理员点击 Web 控制台上的“终止/Kill”按钮时，请求若被负载均衡分配到 Worker B，Worker B 的 `_RUN_PROCESSES` 字典中根本不存在该 `run_id` 的句柄，代码将直接抛出 `RuntimeError: runner handle unavailable; refusing to terminate a stored PID`（出于安全设计，系统拒绝根据持久化的 PID 盲目执行系统 kill，以防误杀复用 PID 的无关进程）。其后果是：**多 Worker 导致任务熔断与紧急停止功能彻底失效，失控的 ACP 子进程持续空转消耗 Token**。

### 2.3 Lifespan 后台守护线程与单例语义冲突
- **源码依据**：`app/main.py:37-73`
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      init_db()
      ...
      if os.environ.get("WECOM_WATCHER", "0") == "1":
          wecom_watcher.start(...)
      if os.environ.get("WECOM_APPROVAL_POLLER", "0") == "1":
          wecom_approval.start(...)
      if os.environ.get("UI_CRON", "1") == "1":
          ui_ops.start_cron()
      if os.environ.get("OCTO_RUNNER", "0") == "1":
          octo_runner.start()
  ```
- **机理分析**：FastAPI 的 Lifespan 生命周期在**每一个 Worker 进程启动时都会被执行一次**：
  1. **UI Cron 定时器**（`app/ui/ops.py:217-227`）：`_cron = _Ticker(...)`，每分钟检查 `scheduled_jobs`。多 Worker 意味着有 N 个独立的 Cron 线程同时运行，导致同一份定时报告（如每日经营简报）被多次重复生成、推送到聊天流中。
  2. **企微推送监听器**（`app/wecom/watcher.py:146-151`）：`Watcher` 线程每 3 秒轮询 `wecom_pushes` 表拉取待发卡片。多 Worker 会出现多个线程同时捞取同一记录，导致企微消息向员工重复推送。
  3. **流水线恢复**（`app/main.py:44`）：`pipeline.recover_interrupted(conn)` 在每个 Worker 启动时都会执行，多进程启动将引发数据恢复竞争。

### 2.4 Gunicorn 与 Uvicorn 的选型取舍
- **Uvicorn 单独运行**（`uvicorn app.main:app --host 127.0.0.1 --port 8077`）：
  - 优势：极为轻量，无任何额外抽象层，天然单进程。
  - 劣势：若进程因 C 扩展崩溃或 OOM 挂掉，需要完全依赖 Systemd 重新拉起；不支持平滑重载（HUP reload）。
- **Gunicorn + UvicornWorker**（`gunicorn -c deploy/gunicorn.conf.py app.main:app`）：
  - 优势：生产推荐。Master 进程专职作为进程看门狗（Process Supervisor），Worker 异常崩溃时由 Master 秒级拉起；支持平滑加载配置（`kill -HUP $MAINPID`）；提供成熟的访问日志格式化与进程生命周期管理。
  - **核心戒律**：必须在 `gunicorn.conf.py` 中写死 `workers = 1`。

---

## 3. Windows 到 Linux 的能力差异与降级配置

Agent OS 最初设计兼顾 Windows 桌面端与沙箱，迁移至 Linux 无头服务端时存在以下能力差异与降级要求：

### 3.1 沙箱机制降级（Windows Low-integrity vs Linux）
- **差异原理**：Windows 端依赖 `tools/sandbox_win.py` 和 `app/engine/lowil.py`，通过 Windows 访问控制令牌与 `icacls ... /setintegritylevel (OI)(CI)L` 强行限制进程只能写入当前任务工作目录。
  查看 `app/engine/dsh_adapter.py:150`：
  ```python
  def sandbox_enabled() -> bool:
      return os.name == "nt" and (os.environ.get("M0_SANDBOX", "lowil").lower() == "lowil")
  ```
  在 Linux 下，`os.name` 为 `'posix'`，因此 `sandbox_enabled()` 恒定返回 `False`，Windows 专有沙箱自动失效。
- **生产降级配置**：
  在 `.env` 中必须显式配置：
  ```ini
  M0_SANDBOX=none
  M0_SANDBOX_ACP=0
  ```
  （如 `tools/serve_ui_test.py:52` 所示）。
- **Linux 侧安全加固替代方案**：
  由于代码层面的 Win32 沙箱已降级，**必须由 Linux 操作系统层面提供安全隔离**：
  1. 使用独立的非特权系统账号（如 `agentos`）运行主进程。
  2. 在 Systemd 单元（`deploy/agent-os.service`）中开启沙箱加固：`ProtectSystem=full`、`ProtectHome=read-only`、`PrivateTmp=true`，仅将 `data/`、`workspace/`、`docs/runs/` 开放为 `ReadWritePaths`。
  3. 如需进一步工具级隔离，可在容器环境（Docker/Podman）或使用 `bwrap`（bubblewrap）包裹后端服务。

### 3.2 DSH CLI 路径适配
- **差异原理**：`app/engine/dsh_adapter.py:40-44` 中 DSH CLI 的默认路径为 Windows 特有：
  `%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\app.asar.unpacked\dsh-cli.cmd`。
  Linux 不存在 `%LOCALAPPDATA%`，且 `.cmd` 脚本无法在 Linux 执行。
- **生产配置**：
  - 若在测试或无模型离线环境：保持 `UI_RUNNER=mock_acp` 与 `RUNNER=mock`，此时不调用 DSH 可执行文件。
  - 若在 Linux 服务器运行真实 ACP 引擎：必须部署 Linux 版本的 DSH CLI 或 Node 包装器，并在 `.env` 中显式指定绝对路径：
    ```ini
    DSH_CLI=/opt/agent-os/bin/dsh-cli
    ```

### 3.3 文件存储路径差异
- **数据库路径**：`app/db.py:14` 默认读取 `DB_PATH` 或 `ROOT/data/oa.db`。Linux 生产环境必须显式指定绝对路径：
  ```ini
  DB_PATH=/opt/agent-os/data/oa.db
  ```
- **工作区路径**：`app/engine/dsh_adapter.py:103` 默认读取 `LOCALAPPDATA` 或用户目录。Linux 环境建议显式指定专用的隔离工作区目录：
  ```ini
  M0_WORKSPACE=/opt/agent-os/workspace
  ```

### 3.4 时区与字符编码
- **时区问题**：`app/ui/ops.py:204-205` 的 UI Cron 调度器使用 `dt.datetime.now()` 读取本地系统时区与当前时间字符串（如 `"09:00"`），并与 `scheduled_jobs` 表的 `at_time` 匹配。若 Linux 服务器默认使用 UTC 时区，将导致北京时间配置的早间报告在下午 17:00 才触发。因此 Systemd 环境中必须显式设置：
  ```ini
  Environment=TZ=Asia/Shanghai
  ```
- **编码问题**：Python 在 Linux 无终端管道环境下若 LANG 未设为 UTF-8，处理大模型中文输入输出或日志打印时可能抛出 `UnicodeEncodeError`。必须指定：
  ```ini
  Environment=PYTHONIOENCODING=utf-8
  Environment=LANG=C.UTF-8
  Environment=LC_ALL=C.UTF-8
  ```

---

## 4. 系统环境与基础依赖

推荐操作系统：Ubuntu 22.04 LTS / Debian 12 / Rocky Linux 9。

### 4.1 安装系统基础包
以 Ubuntu / Debian 为例：
```bash
sudo apt update && sudo apt install -y \
    python3 \
    python3-venv \
    python3-dev \
    sqlite3 \
    nodejs \
    npm \
    nginx \
    curl \
    git \
    build-essential
```

### 4.2 创建专用运行用户与目录结构
生产环境严禁使用 `root` 运行应用：
```bash
# 创建系统运行用户 agentos (无登录 shell)
sudo useradd -r -s /usr/sbin/nologin -d /opt/agent-os -m agentos

# 创建业务运行所需的基础目录
sudo mkdir -p /opt/agent-os/data
sudo mkdir -p /opt/agent-os/workspace
sudo mkdir -p /opt/agent-os/docs/runs
sudo mkdir -p /opt/agent-os/backups
sudo mkdir -p /var/log/agent-os

# 设置所有权与基础掩码
sudo chown -R agentos:agentos /opt/agent-os
sudo chown -R agentos:agentos /var/log/agent-os
sudo chmod 750 /opt/agent-os
```

---

## 5. 前后端构建与安装步骤

### 5.1 代码检出
```bash
cd /opt
sudo -u agentos git clone -b mainline https://your-git-repo/oaagent-m0.git /opt/agent-os
cd /opt/agent-os
```

### 5.2 Python 虚拟环境与依赖安装
```bash
# 创建虚拟环境
sudo -u agentos python3 -m venv /opt/agent-os/.venv

# 升级 pip 并安装核心依赖
sudo -u agentos /opt/agent-os/.venv/bin/pip install --upgrade pip
sudo -u agentos /opt/agent-os/.venv/bin/pip install -r requirements.txt

# 安装生产进程管理器 Gunicorn
sudo -u agentos /opt/agent-os/.venv/bin/pip install gunicorn
```

### 5.3 前端 Vite 控制台构建
`web/` 目录为 React 18 + Vite 项目，构建产物将放置在 `web/dist`：
```bash
cd /opt/agent-os/web

# 安装前端依赖
sudo -u agentos npm ci

# 执行 Vite 打包编译
sudo -u agentos npm run build

# 验证构建产物是否存在
ls -lh /opt/agent-os/web/dist/index.html
```

> **产物挂载机制说明**：
> `app/main.py:102-104` 包含原生检测逻辑：
> ```python
> _UI_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
> if _UI_DIST.exists():
>     app.mount("/app", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")
> ```
> 只要 `web/dist` 保持在仓库内，FastAPI 服务启动时便能原生在 `/app` 路径下提供 Web 访问；而在生产架构中，Nginx 将优先通过 `alias /opt/agent-os/web/dist/` 进行静态托管，降低后端 Python 进程的 IO 负载。

---

## 6. 首次初始化与身份签发

### 6.1 生成生产配置文件 `.env`
从 `deploy/.env.production.example` 复制模板：
```bash
sudo -u agentos cp /opt/agent-os/deploy/.env.production.example /opt/agent-os/.env
sudo chmod 600 /opt/agent-os/.env
```

生成并配置全局 `UI_SESSION_SECRET`（64位随机字符串，用于 HMAC 登录 Cookie 与 Bearer 令牌签名）：
```bash
HEX_SECRET=$(/opt/agent-os/.venv/bin/python3 -c "import secrets; print(secrets.token_hex(32))")
sudo -u agentos sed -i "s/YOUR_GENERATED_64_CHAR_HEX_SECRET_HERE/$HEX_SECRET/" /opt/agent-os/.env
```
根据实际网络情况，在 `/opt/agent-os/.env` 中配置 `DEEPSEEK_API_KEY` 等参数。

### 6.2 数据库初始化与表结构迁移
运行数据库初始化，创建 SQLite 表结构并执行增量字段迁移：
```bash
sudo -u agentos /opt/agent-os/.venv/bin/python3 -c "from app.db import init_db; init_db()"
```
执行后，将在 `/opt/agent-os/data/` 下生成 `oa.db`、`oa.db-wal`、`oa.db-shm` 三个文件。

### 6.3 签发初始管理员（CEO）身份
通过仓库自带的 `tools/auth_admin.py` 工具签发管理员凭据：
```bash
cd /opt/agent-os
sudo -u agentos /opt/agent-os/.venv/bin/python3 tools/auth_admin.py issue --name admin-master --role ceo
```
**终端输出示例**（以下为占位符，真实值以你的终端输出为准）：
```text
id=ID-xxxxxxxxxxxx
role=ceo
secret=<64 位十六进制随机串>

# add to .env (never commit):
OA_ID=ID-xxxxxxxxxxxx
OA_SECRET=<同上面那串 secret>
```
- **记录保存**：请务必安全保存输出的 `id` 与 `secret`（该密钥仅在签发时打印一次，数据库仅存储哈希值校验）。
- **用途**：
  1. 打开浏览器访问 Web 控制台（`/app/`），在登录界面使用该 `id` 和 `secret` 登录。
  2. 若后端定时任务或内部自动化工具需要调用 API，可将 `OA_ID` 与 `OA_SECRET` 追加写入 `/opt/agent-os/.env`。

---

## 7. Systemd 守护进程与 Gunicorn/Uvicorn 配置

### 7.1 安装 Systemd 服务文件
将预置的 `deploy/agent-os.service` 部署到系统服务目录：
```bash
sudo cp /opt/agent-os/deploy/agent-os.service /etc/systemd/system/agent-os.service
sudo systemctl daemon-reload
```

### 7.2 启动与自启设置
```bash
# 启用开机自启并立即启动
sudo systemctl enable --now agent-os

# 查看服务运行状态
sudo systemctl status agent-os
```

### 7.3 Gunicorn vs Uvicorn 运行命令对照
- **Gunicorn 方案（推荐，已固化在 service 文件中）**：
  使用 `deploy/gunicorn.conf.py`，命令为：
  ```bash
  /opt/agent-os/.venv/bin/gunicorn -c deploy/gunicorn.conf.py app.main:app
  ```
- **Uvicorn 单独运行方案**：
  若不希望引入 Gunicorn，直接将 service 中的 `ExecStart` 替换为：
  ```bash
  /opt/agent-os/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077 --workers 1
  ```

---

## 8. Nginx 站点配置与反向代理

### 8.1 安装 Nginx 配置文件
```bash
sudo cp /opt/agent-os/deploy/nginx.conf /etc/nginx/sites-available/agent-os.conf
sudo ln -sf /etc/nginx/sites-available/agent-os.conf /etc/nginx/sites-enabled/agent-os.conf

# 测试配置并重载 Nginx
sudo nginx -t
sudo systemctl reload nginx
```

### 8.2 Nginx 核心配置点解析
1. **SPA 路由回退（`try_files $uri $uri/ /app/index.html;`）**：
   用户在 React 控制台中刷新非根路由页面时（如 `/app/?tab=chat`），Nginx 会正确回退到 `index.html`，交由客户端 React Router 解析，避免 404 错误。
2. **静态文件强缓存**：
   `/app/assets/` 采用 Vite 构建产生的哈希文件名，配置 `Cache-Control "public, max-age=2592000, immutable"`（缓存 30 天）；HTML 入口文件配置 `Cache-Control "no-store"` 确保版本更新即刻生效。
3. **SSE 流式传输（`/ui/chats/{chat_id}/stream`）**：
   - 必须配置 `proxy_buffering off;`，否则 Nginx 会默认缓冲后端输出，直到缓冲区满后才一次性发送，彻底破坏大模型打字机式的流式体验。
   - 配置 `proxy_read_timeout 3600s;`，防止网关在默认 60s 内无新消息时切断长连接。
4. **附件上传限额**：
   配置 `client_max_body_size 50M;`，避免用户在聊天窗口上传大型设计方案或数据附件时触发 HTTP 413 Payload Too Large。
5. **绝对安全隔离**：
   显式拒绝所有以 `.env*`、`.git`、`*.db`、`*.wal`、`*.py` 结尾的请求，并封禁对 `/data`、`/workspace`、`/docs` 等本地核心目录的 URL 映射，防止数据勒索或配置泄露。

---

## 9. 服务验证与健康检查

部署完成后，在服务器本地执行以下验证流程：

### 9.1 健康检查接口
```bash
curl -i http://127.0.0.1:8077/health
```
**期望响应**：
```http
HTTP/1.1 200 OK
content-type: application/json

{"ok":true,"service":"oaagent-m0"}
```

### 9.2 前端入口与静态资源验证
```bash
curl -i http://127.0.0.1/app/
```
**期望响应**：HTTP 200 且返回包含 `<div id="root"></div>` 的 HTML 页面。

### 9.3 登录鉴权测试
使用签发的管理员凭据发起 POST 登录：
```bash
curl -i -X POST http://127.0.0.1:8077/ui/login \
  -H "Content-Type: application/json" \
  -d '{"identity_id":"ID-xxxx","secret":"your-secret"}'
```
**期望响应**：HTTP 200，Set-Cookie 返回 `oa_ui=...; HttpOnly; Path=/`，且 JSON 包含 `token` 字段。

---

## 10. 运维管理、日志与版本升级

### 10.1 服务启停与重载
```bash
# 查看实时运行日志
sudo journalctl -u agent-os -f

# 平滑重载配置 (向 Gunicorn 发送 SIGHUP)
sudo systemctl reload agent-os

# 重启后端服务
sudo systemctl restart agent-os
```

### 10.2 任务执行追踪日志
每个任务执行过程（Token 消耗、工具调用轨迹、审计信息）保存在：
```bash
ls -lh /opt/agent-os/docs/runs/*.jsonl
```
运维工程师可随时对正在执行的任务进行追踪排查：
```bash
tail -f /opt/agent-os/docs/runs/<RUN_ID>.live.jsonl
```

### 10.3 标准化平滑升级流程
当仓库发布新版本（例如从 `v3.2.1` 升级）时，按以下标准 SOP 操作：

```bash
# 1. 切换到项目根目录
cd /opt/agent-os

# 2. 拉取最新代码
sudo -u agentos git fetch origin mainline
sudo -u agentos git checkout mainline && sudo -u agentos git pull

# 3. 升级 Python 依赖
sudo -u agentos /opt/agent-os/.venv/bin/pip install -r requirements.txt

# 4. 重新构建前端静态资源
cd /opt/agent-os/web
sudo -u agentos npm ci
sudo -u agentos npm run build

# 5. 执行数据库迁移 (app/db.py 内置 MIGRATIONS 机制，幂等安全)
cd /opt/agent-os
sudo -u agentos /opt/agent-os/.venv/bin/python3 -c "from app.db import init_db; init_db()"

# 6. 重启服务完成上线
sudo systemctl restart agent-os

# 7. 检查服务健康度
curl -s http://127.0.0.1:8077/health | grep '"ok":true'
```

---

## 11. SQLite 在线热备与灾难回滚

### 11.1 为什么严禁使用直接 `cp` 拷贝？
在 SQLite WAL 模式下，活跃的写事务存在于 `oa.db-wal` 临时日志中。如果直接使用 Linux `cp oa.db ...`，极大概率复制出处于不一致中间态的损坏镜像。

### 11.2 生产在线热备标准方案（VACUUM INTO / .backup）
SQLite 提供了在线安全备份指令，能够在系统持续运行、并发读取的同时生成一致性快照：

**方案 A：使用 SQLite 命令行 `.backup`（推荐）**
```bash
BACKUP_FILE="/opt/agent-os/backups/oa_$(date +%Y%m%d_%H%M%S).db"
sudo -u agentos sqlite3 /opt/agent-os/data/oa.db ".backup '$BACKUP_FILE'"
```

**方案 B：使用 SQL `VACUUM INTO` 语句**
```bash
BACKUP_FILE="/opt/agent-os/backups/oa_vacuum_$(date +%Y%m%d_%H%M%S).db"
sudo -u agentos sqlite3 /opt/agent-os/data/oa.db "VACUUM INTO '$BACKUP_FILE';"
```

**定时备份 Cron 配置示例**：
```bash
# 编辑 agentos 用户定时任务: crontab -u agentos -e
# 每天凌晨 02:00 自动备份，并清理 30 天前的旧备份
0 2 * * * sqlite3 /opt/agent-os/data/oa.db ".backup '/opt/agent-os/backups/oa_\$(date +\%Y\%m\%d_\%H\%M\%S).db'" && find /opt/agent-os/backups -name "oa_*.db" -mtime +30 -delete
```

### 11.3 灾难恢复与回滚操作 SOP
当数据库发生逻辑损坏或误删数据需要回滚时，必须严格遵循以下顺序：

```bash
# 步骤 1: 必须首先停止服务，切断所有读写连接
sudo systemctl stop agent-os

# 步骤 2: 将故障现场做临时归档
cd /opt/agent-os/data
sudo -u agentos mkdir -p /opt/agent-os/backups/corrupted_$(date +%Y%m%d_%H%M%S)
sudo -u agentos mv oa.db* /opt/agent-os/backups/corrupted_$(date +%Y%m%d_%H%M%S)/

# 步骤 3: 恢复指定的备份文件为 oa.db
sudo -u agentos cp /opt/agent-os/backups/oa_20261020_020000.db /opt/agent-os/data/oa.db

# 步骤 4: 【极其关键】彻底确认清理旧的 WAL 与 SHM 文件！
# 若保留旧的 oa.db-wal 或 oa.db-shm，SQLite 启动时会强行回放日志，导致刚恢复的数据被覆盖损坏！
sudo rm -f /opt/agent-os/data/oa.db-wal
sudo rm -f /opt/agent-os/data/oa.db-shm

# 步骤 5: 校准权限
sudo chown agentos:agentos /opt/agent-os/data/oa.db
sudo chmod 640 /opt/agent-os/data/oa.db

# 步骤 6: 启动服务
sudo systemctl start agent-os

# 步骤 7: 检查服务健康度
curl -i http://127.0.0.1:8077/health
```

---

## 12. 环境变量速查字典（生产核准）

本表列出系统在生产部署中涉及的所有关键环境变量，均已通过 `app/config.py`、`app/main.py` 及相关核心代码逐一校验，不存在未定义配置项：

| 环境变量 | 默认值 / 建议值 | 代码位置 | 功能说明 |
| :--- | :--- | :--- | :--- |
| `UI_SESSION_SECRET` | 随机64位十六进制 | `app/ui/api.py:38` | 用于 Web 端 Cookie 与 Token HMAC 签名（生产必填） |
| `UI_DEV_LOGIN` | `0` | `app/ui/api.py:116` | 开发免密登录开关（生产环境必须为 `0`） |
| `DB_PATH` | `/opt/agent-os/data/oa.db` | `app/db.py:14` | SQLite 数据库文件绝对路径 |
| `M0_WORKSPACE` | `/opt/agent-os/workspace` | `app/engine/dsh_adapter.py:104` | 任务执行临时目录根路径 |
| `M0_SANDBOX` | `none` | `app/engine/dsh_adapter.py:150` | 沙箱类型（Linux 下不支持 Win32 lowil，填 `none`） |
| `M0_SANDBOX_ACP` | `0` | `app/engine/runner_acp.py:142` | ACP 外部沙箱包装开关（Linux 下填 `0`） |
| `DSH_CLI` | `/opt/agent-os/bin/dsh-cli` | `app/engine/dsh_adapter.py:40` | Linux 下 DSH CLI 执行文件绝对路径 |
| `UI_RUNNER` | `mock_acp` 或 `acp` | `app/ui/api.py:646` | UI 发起任务时的执行器类型 |
| `RUNNER` | `mock` 或 `acp` | `app/api/tasks.py:215` | 后台核心任务执行器类型 |
| `AUTO_APPROVE` | `0` | `app/ui/api.py:677` | 审批自动化跳过开关（生产环境建议为 `0`） |
| `UI_TASK_BUDGET` | `80000` | `app/ui/api.py:506` | 单个任务的默认 Token 消耗上限 |
| `M0_RUN_TIMEOUT` | `240` | `app/engine/dsh_adapter.py:559` | 单个任务最大执行超时秒数 |
| `M0_HEARTBEAT_SECONDS`| `15` | `app/engine/dsh_adapter.py:560` | 运行状态心跳上报间隔秒数 |
| `M0_LOCK_TTL` | `1800` | `app/locks.py:23` | 任务锁与会话锁超时 TTL 秒数 |
| `DEEPSEEK_API_KEY` | - | `app/llm.py:17` | DeepSeek 大模型推理密钥 |
| `LLM_API_BASE` | `https://api.deepseek.com/v1`| `app/ui/admin.py:259` | 大模型 API 端点基础 URL |
| `LLM_MODEL` | `deepseek-flash` | `app/finops.py:30` | 默认调用的模型名称 |
| `UI_CRON` | `1` | `app/main.py:61` | 是否随 Lifespan 启动后台定时任务调度器 |
| `UI_CRON_TICK` | `60` | `app/ui/ops.py:226` | UI 定时任务轮询步进秒数 |
| `WECOM_WATCHER` | `0` | `app/main.py:45` | 企微消息推送后台轮询守护线程开关 |
| `WECOM_APPROVAL_POLLER`| `0` | `app/main.py:55` | 企微审批状态后台轮询守护线程开关 |
| `OCTO_RUNNER` | `0` | `app/main.py:66` | Octo 自治集群任务执行器开关 |
| `DEFAULT_LEGAL_ENTITY_ID`| `LE-GROUP-HQ` | `app/models.py:9` | 默认法人实体与集团总部代号 |
| `OA_BASE` | `http://127.0.0.1:8077` | `app/wecom/bridge.py:23` | 内部反向回调服务基础地址 |

| `WECOM_ROBOT_WEBHOOK` | 空（建议用界面按租户保存） | `app/wecom/robot.py:106` | 企微群机器人 webhook；9/9 闭环与红线拦截的推送通道 |
| `WECOM_ROBOT_DRY_RUN` | `0` | `app/wecom/robot.py:59` | 置 `1` 只写本地 `wecom_robot_outbox`，不真的发群（联调/演练） |
| `UI_PUBLIC_URL` | `http://127.0.0.1:8077` | `app/notify.py:51` | 通知正文里的工作台链接前缀 |
| `NOTIFY_SYNC` | `0` | `app/notify.py:89` | 置 `1` 同步投递（测试/CLI 用），默认后台线程 |

> `WECOM_ROBOT_ALLOW_CUSTOM_HOST=1` 仅供自建网关使用（`app/wecom/robot.py:77`）：
> 打开后允许非 `qyapi.weixin.qq.com` 的地址，等于放开一个 SSRF 面，生产默认不要设。

## 13. 企微群机器人通知（v4.0）

两类业务事件会实时推送到企微群，走的是**群机器人 webhook**（一条 URL，不依赖 corp 应用的
AccessToken/AgentId，也不需要员工 userid），因此它和第七章的企微审批通道互不影响。

| 事件 | 触发点 | 正文要点 |
| :--- | :--- | :--- |
| 商业闭环 9/9 | `app/pipeline.py:931`（`run_project` 跑完最后阶段后） | 项目名/ID、阶段 9/9、闸门通过数、实体与租户、工作台链接 |
| 红线 L0 拦截 | `app/acceptance_run.py:252`（验收判定之后） | 任务名/ID、命中禁用词、判据、Run、**没有人工审批入口** |

语义（照实现写，不承诺没做的事）：

1. **先记账再投递。** 事件先写 `notifications`（唯一键 `tenant_id+event+ref`），再由后台线程投递；
   同一项目闭环只推一次，同一 Run 的红线拦截只推一次，换一次 Run 会再推（新信息）。
2. **推送不是闸门。** 机器人未配置/停用/网络失败时，`notifications.state` 记 `failed`，
   任务照常驳回、项目照常推进；HTTP 超时 8 秒且跑在 daemon 线程里，不会拖住请求。
3. **配置按租户隔离。** `wecom_robots` 表按 `tenant_id` 存；只有 CEO/system 能改；
   读接口只回 `webhook_set` + 末 6 位提示，不回整串 key（key 本身就是凭据）。
4. **只认官方端点。** 地址必须 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…`，
   防止管理员误填内网地址形成 SSRF。
5. **两类推送可分别关闭。** `on_project_done` / `on_redline` 关掉后连通知行都不落（视为不需要）。

### 13.1 配置

界面：`工具与集成 → 企微群机器人`（仅 CEO/system 可见），填 webhook、勾选两类事件、保存后可点
**发送测试**。接口等价于：

```bash
curl -s -X PUT http://127.0.0.1:8077/ui/wecom/robot \
  -H "authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"webhook":"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>",
       "enabled":true,"on_project_done":true,"on_redline":true}'
```

### 13.2 验证

```bash
curl -s http://127.0.0.1:8077/ui/wecom/robot -H "authorization: Bearer $TOKEN"   # 只看 set/hint
curl -s -X POST http://127.0.0.1:8077/ui/wecom/robot/test -H "authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8077/ui/notifications?limit=20" -H "authorization: Bearer $TOKEN"
```

离线验收（不接真群）：`WECOM_ROBOT_DRY_RUN=1` 后跑
`pytest tests/test_v4_notifications.py -q`——13 项覆盖配置面、9/9 闭环、红线拦截、
去重、开关关闭、推送失败不影响驳回，以及 9 阶段真跑（stub 执行）后自动落一条闭环通知。

