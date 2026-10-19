# DSH headless 事件流（实测样本）

抓取命令（Windows，必须走 stdin）：

```bash
CLI="$LOCALAPPDATA/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd"
export DEEPSEEK_API_KEY=...        # headless 不读桌面端凭据库
printf '写一篇 500 字秋季护肤短文' | "$CLI" --profile headless --json - > sample.jsonl
echo exit=$?
```

- `exit 0` = 任务完成；`exit != 0` = 中止或出错。
- 不开端口、不留后台进程，一次调用一个任务。
- 原始样本：`docs/raw/sample2.jsonl`（本机抓取，含一次真实写文件）。

## 事件表

| type | 字段 | 外壳怎么用 |
|---|---|---|
| `session` | `sessionId`, `cwd` | 存 `task_runs.session_id`，可作 `--session-id` 续跑 |
| `status` phase=`turn_start` | `turn` | 忽略（run 已由外壳标记 in_progress） |
| `status` phase=`step_start` | `turn`, `step` | 忽略 |
| `thinking` | `text` | 只入日志，不解析 |
| `text` | `text` | 助手可见文本 |
| `tool_call` | `callId`, `tool`, `input.file_path` | **产出物来源**：`tool in {write,edit,...}` 且路径存在 → `attach_artifact` |
| `tool_result` | `callId`, `status`, `result` | 校验调用是否 completed |
| `status` phase=`step_end` | `usage.{inputTokens,outputTokens,cacheReadTokens,cacheWriteTokens,totalTokens}` | 预算结算取 `max(totalTokens)` |
| `status` phase=`turn_end` | `reason.kind` | 结束原因 |
| `final` | `text` | 最终答案 |

## 两条必踩的坑

1. **位置参数会炸**：`dsh --profile headless --json "带空格的指令"` → `'C:\...\Programs\DeepSeek' 不是内部或外部命令`。
   正确做法是 `... --json -` + stdin。
2. **凭据**：桌面端能跑不代表 CLI 能跑，缺 key 时报 `MISSING_CREDENTIAL`，需要在启动环境导出 `DEEPSEEK_API_KEY`。

## 与六动词的对应

| 外壳动作 | 触发点 | 说明 |
|---|---|---|
| `spawn_run` | 子进程启动前 | 外壳主动创建，没有 `agent.started` |
| `emit_event(run_started)` | Popen 成功后 | |
| `report_heartbeat` | 外壳 watchdog 每 N 秒 | headless 没有心跳事件 |
| `attach_artifact` | 进程结束后按 `tool_call` 路径落盘哈希 | 不信任模型自报的 checksum |
| `emit_event(run_succeeded)` | `exit_code == 0` | |
| `emit_event(run_failed)` | `exit_code != 0` / 超时 / 预算拒绝 | 预算拒绝时**不 spawn** |
| `reserve_budget` | spawn 前一次；续跑前再一次 | 不是每次 LLM 调用 |
