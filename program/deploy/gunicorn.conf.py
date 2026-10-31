# ==============================================================================
# Agent OS (oaagent-m0) - Gunicorn 生产配置
# ==============================================================================
# 启动命令:
#   gunicorn -c deploy/gunicorn.conf.py app.main:app
# ==============================================================================

import multiprocessing

# 监听地址与端口（仅限本机回环，前端由 Nginx 反向代理）
bind = "127.0.0.1:8077"

# ------------------------------------------------------------------------------
# 架构硬约束：Worker 数量必须且只能为 1！
# ------------------------------------------------------------------------------
# 严禁将 workers 设为 > 1（即使多核 CPU 也必须保持 workers = 1）。
# 原因支撑：
# 1. SQLite 单写者锁限制 (app/db.py:27): SQLite WAL 模式虽然支持并发读，但写操作
#    全局互斥。多进程并发写入极易触发 "sqlite3.OperationalError: database is locked"。
# 2. 进程内句柄注册表 (app/engine/dsh_adapter.py:50): ACP 子进程 Popen 句柄保存在单进程
#    内存字典 `_RUN_PROCESSES` 中。多 Worker 架构下，若 Stop/Kill 任务的 HTTP 请求打到
#    非属主 Worker，将直接抛出 RuntimeError 并导致失控子进程孤立运行。
# 3. Lifespan 守护线程单例语义 (app/main.py:37-73): UI Cron 调度、企微消息推送、企微审批
#    轮询等后台线程随 FastAPI Lifespan 启动。多 Worker 将导致多个实例同时轮询并引发重复推送
#    与定时任务竞争执行。
# ------------------------------------------------------------------------------
workers = 1

# 使用 Uvicorn ASGI Worker
worker_class = "uvicorn.workers.UvicornWorker"

# 长任务超时保护（秒）
# 部分大模型 ACP 推理或多轮工具调用耗时较长，预留充足超时时间
timeout = 600
graceful_timeout = 30
keepalive = 65

# 日志配置（标准输出与标准错误交由 systemd journal 统一捕获与轮转）
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" (%(D)s µs)'

# 进程标识
proc_name = "agent-os"
