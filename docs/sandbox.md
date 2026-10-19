# 沙箱：任务专属目录 + Windows Low 完整性级别

目标：Agent 只在 `workspace/<task_id>/` 里写，出了这个目录**只能读**。拦截点在操作系统，不在提示词里。

## 机制（三步，全部是 OS 层）

1. **任务专属目录**：每次跑任务，cwd = `workspace/<task_id>/`。两个任务不可能共享目录。
2. **目录打 Low 标签**：`icacls <dir> /setintegritylevel (OI)(CI)L`。
   只有打了 Low 标签的对象，Low 进程才能写。
3. **子进程降到 Low**：`tools/sandbox_win.py` 先把**自己**的令牌完整性级别设为 Low
   （降低级别不需要任何特权），再用普通 `subprocess` 拉起真正的命令 —— 子进程继承这个令牌，
   整棵进程树都在 Low。

Windows 的强制完整性控制默认策略是 **no write up**：Low 进程能读 Medium 对象（读下可以），
但不能写。于是：

| 位置 | Agent 能读 | Agent 能写 |
|---|---|---|
| `workspace/<task_id>/`（Low） | ✅ | ✅ |
| `workspace/` 根、`C:\oaagent-m0` 其余、用户目录 | ✅ | ❌ `PermissionError` |
| `C:\Windows`、程序目录 | ✅ | ❌ |

这不是提示词约束，是内核在 `CreateFile` 时直接返回 `ERROR_ACCESS_DENIED`。

## 环境隔离

launcher 还把临时目录和 DSH 的家目录一起搬进任务目录：

```
TEMP / TMP / TMPDIR = <task>/.tmp      （Low）
DSH_HOME            = <task>/.dsh      （Low，profile/session 都落在这里）
```

副作用：**每个任务有自己的 DSH profile 和 session**，所以跨任务不能 resume，同一任务内可以
（retry 走的就是同一个 `workspace/<task_id>/.dsh`）。这是隔离换来的，不是 bug。

## 开关

```
M0_SANDBOX=lowil   # 默认（Windows）；关闭用 none
```

代码位置：`app/engine/dsh_adapter.py` 的 `sandbox_cmd()`；launcher 在 `tools/sandbox_win.py`。

## 自测

```bash
# 原语：进得去、出不来
.venv\Scripts\python.exe tools\sandbox_win.py --selftest
# => rc=0 inside_written=True escaped=False

# 端到端：让 Agent 自己尝试写 ..\outside.txt
.venv\Scripts\python.exe scripts\run_sandbox_case.py
```

pytest 里的两条对应用例：`test_m0_19_low_integrity_blocks_writes_outside`（原语）、
`test_m0_20_runner_cannot_escape_its_workspace`（走完整 runner 路径）。

## 已知边界

- **读是放开的**。要"看不见"而不是"只能读"，得换 AppContainer（按包 SID 授权），工作量另算。
- **给目录打标签需要 WRITE_OWNER**。UAC 过滤令牌下直接 icacls 会 `拒绝访问`，
  launcher 会退化成"只对这一条 icacls 提权"（本机 UAC 静默，别处会弹一次）。
  Agent 本身始终以 Medium 以下的普通权限运行，提权只发生在建目录这一瞬间。
- **DSH 会写自己的家目录**，所以 `DSH_HOME` 必须重定向，否则它在 Low 下启动即失败。
- 沙箱只约束文件写入。网络、进程、CPU/内存不在这一层（要 Job Object 补，未做）。
