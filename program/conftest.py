import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Safe test isolation defaults: prevent tests from spawning real DSH/ACP runners
# or background polling threads during test execution.
_SAFE_TEST_DEFAULTS = {
    "RUNNER": "mock",
    "UI_RUNNER": "mock",
    "WECOM_MOCK": "1",
    "WECOM_AUTORUN": "0",
    "WECOM_WATCHER": "0",
    "WECOM_APPROVAL_POLLER": "0",
    "OCTO_RUNNER": "0",
    "OCTO_AUTORUN": "0",
    "UI_CRON": "0",
    # v1 事件/Agent：测试里不自动触发模型回合（会起后台线程 + 打网络）。
    "AGENT_AUTORUN": "0",
    # v5.3：NativeKernel 默认双模。测试一律走确定性模板分支——
    # 秒级、零外网、可复现；要用真模型必须在用例里显式 monkeypatch 这两个变量。
    "M0_NATIVE_MODE": "template",
    "M0_MOCK_LLM": "1",
    # v5.4 规则 4：L1 人工验收闸默认严格（无 evidence_snippet 不放行）。
    # 存量用例大多依赖「流程自动跑到 done」，这个开关只给测试通道用，
    # 生产环境严禁开启；一旦被开启，每次自动签批都会写 l1_auto_signed_for_test 审计。
    # tests/test_v54_l1_human_gate_strict.py 会显式关掉它来验证严格路径。
    "M0_AUTO_SIGN_L1_FOR_TEST": "1",
}
for _k, _v in _SAFE_TEST_DEFAULTS.items():
    os.environ[_k] = _v

# ---------------------------------------------------------------- 测试必须与 .env 隔离
# app/main.py 在 import 时调用 config.load_env()，它用 os.environ.setdefault 把开发者本机
# 的 DEEPSEEK_API_KEY / 企微 / OCTO 等灌进进程环境。后果有两个，都不可接受：
#   1) 部分用例会真的走网络 —— app/llm.py 的 complete() 没有 mock 开关，只要有 key 就发
#      HTTP，跑一次测试就是一次真实计费调用，且结果不可复现；
#   2) 「测试是否通过」会取决于这台机器上有没有 .env —— 干净 checkout 上必红。
# 在 app.main 被 import 之前把 load_env 变成 no-op，测试环境完全由本文件决定。
import app.config as _config  # noqa: E402  (sys.path 已在上面就位)

_config.load_env = lambda *_a, **_k: {}

# 避免 pytest 递归进工作区扫描生成代码中的测试
collect_ignore_glob = ["workspace/*"]

