# DSH ACP 协议（实测，`dsh --profile acp`）

stdio + 按行分帧的 JSON-RPC 2.0。客户端是外壳，服务端是 DSH。

## 方法

| 方向 | 方法 | 参数 | 返回 |
|---|---|---|---|
| C→S | `initialize` | `{protocolVersion:1, clientCapabilities:{fs,terminal}}` | `{agentInfo, agentCapabilities:{sessionCapabilities:{close,list,resume}}, authMethods:[]}` |
| C→S | `session/new` | `{cwd, mcpServers:[]}` | `{sessionId, configOptions:[model…]}` |
| C→S | `session/resume` | `{sessionId, cwd}` | `{configOptions}` — 跨进程续上下文 |
| C→S | `session/prompt` | `{sessionId, prompt:[{type:"text",text}]}` | `{stopReason:"end_turn"}` |
| C→S | `session/close` | `{sessionId}` | 释放会话写句柄 |
| C→S | `session/cancel`（通知，无 id） | `{sessionId}` | 无返回；当前轮终止 |

## 服务端推的通知 `session/update`

| sessionUpdate | 字段 | 用途 |
|---|---|---|
| `agent_thought_chunk` | `messageId`, `content.text` | 思考流，只入日志 |
| `agent_message_chunk` | `messageId`, `content.text` | 最终回答的增量文本 |
| `tool_call` | `toolCallId`, `title`, `kind`, `status`, `rawInput.file_path` | 产出物来源（`title=write`） |
| `tool_call_update` | `toolCallId`, `status`, `content` | 调用完成/失败 |
| `usage_update` | `used`, `size` | **每次模型调用后**推一次，预算闸的唯一输入 |

## 五条实测结论

1. **`usage_update.used` 是"按会话累计"，不是"按轮次"。**
   同一会话第一轮 9729→11772；`session/resume` 后第二轮 12112→13077。
   所以闸门要算 `baseline + 本次预留`，结算只能算 `used - baseline`（外壳把两者都落库：
   `task_runs.usage_tokens` = 累计值，`usage_baseline` = 开跑前的基线）。

2. **没有 `session/request_permission`。** 写文件的 `tool_call` 直接执行，客户端拿不到逐工具审批。
   也就是说 ACP 能拦的是"模型调用之后、下一次调用之前"，不是"工具执行前"。

3. **stdout 混有非 JSON 日志行**，例如
   `[llm-deepseek-account] model listing from https://api.deepseek.com/models loaded 2 model(s)`。
   逐行 `json.loads` 的客户端会在这里崩掉，必须跳过非 `{` 开头的行。

4. **`usage_update` 是"事后"信号。** 第一笔 `used` 到达时，那次调用已经花掉了。
   外壳能保证的是：一旦 `used > cap` 立刻 `session/cancel`，本轮后续调用不再发生。
   已经飞出去的那一次拦不住 —— 实测：cap 2000 的跑法在 9264 tokens 处被取消，超支 7264。
   零超支只有一条路：DSH 插件挂在 `agent/request` waterfall 上（见 DESIGN_GAPS §13）。

5. **杀进程不能只杀 `.cmd`。** `dsh.cmd` 是批处理壳，`Popen.kill()` 只杀 cmd.exe，
   electron 子进程继续活着并持有会话写句柄，下一次 `session/resume` 会报
   `session "…" is already owned by an active write handle`。
   正确收尾：`session/close` → 关 stdin → 等 5s → 还不退就 `taskkill /F /T /PID`。
