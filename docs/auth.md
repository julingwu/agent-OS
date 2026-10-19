# 身份与签名（M1）

每个 API 请求都要带 HMAC-SHA256 签名。没有签名 = 401，签名不对 = 401，重放 = 401。
签名里的身份决定"谁签的字"，请求体里的 `decided_by` 一律忽略。

## 签名串

```
METHOD             POST
PATH               /decisions/DP-xxx/decide
sorted-query       state=escalated_ceo&limit=10   （无则空串）
timestamp          1760000000
nonce              3f2a…                          （uuid4 hex，一次有效）
sha256-hex(body)   e3b0c442…                      （无 body 时为 sha256("")）
```

六行用 `\n` 连接，`HMAC-SHA256(secret, 上面那串)` 的 hex 就是 `X-OA-Sig`。

请求头：`X-OA-Key`（身份 id）、`X-OA-Ts`、`X-OA-Nonce`、`X-OA-Sig`。

## 防重放

- 时间窗口：`|now - ts| <= 300s`，超出回 `SignatureExpired`。
- nonce：写进 `auth_nonces` 主键，重复即 `ReplayDetected`。
- 两者都在同一事务里落库，重放不会产生任何业务效果。

## 角色与 scope

| 角色 | 能做什么 |
|---|---|
| `system` | 全权（运维/脚本） |
| `ceo` | 全部 + 只能由 CEO 拍板的事项 |
| `manager` | 任务、验收判定、决策创建/评估、主管级决策 |
| `agent` | 建任务、六动词、创建/评估决策；**不能**判定验收、**不能**拍板 |

关键绑定：

- `POST /criteria/{id}/check`（L1 人工判定）→ `checked_by` = 签名身份，不是请求体。
- `POST /decisions/{id}/decide` →
  - `escalated_ceo` 只有 `ceo`（或 `system`）能批；
  - `escalated_manager` 只有 `manager`/`ceo`（或 `system`）能批；
  - `decided_by` = 签名身份，审计里同时记 `role`。

## 签发

```bash
python tools/auth_admin.py issue --name ceo-01 --role ceo   # secret 只打印一次
python tools/auth_admin.py list
python tools/auth_admin.py revoke --id ID-xxx
```

脚本用 `.env` 里的 `OA_ID` / `OA_SECRET`（`tools/oa_client.py` 自动加载并签名）。

## 工具调用策略（补 ACP 缺口）

ACP 不发 `session/request_permission`，但 `tool_call` 通知在工具**完成前**就到，且带 `rawInput`。
所以外壳能做两件事：

1. 写类工具的目标路径不在任务目录内 → 立刻 `session/cancel`，本次 run 判 `tool_policy_violation`，
   审计写 `verb=tool_policy`。
2. 兜底仍是 Low 完整性沙箱：即使取消慢了一步，写到目录外也只会得到 `PermissionError`。

`M0_DENY_TOOLS=bash,pwsh` 可以整类禁掉工具（默认空）。
