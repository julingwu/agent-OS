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

