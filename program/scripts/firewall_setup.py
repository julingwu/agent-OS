"""收敛 Lighthouse 防火墙：删掉重装前遗留的 8899，开 443。

账号/令牌鉴权由 AgentOS 自己做，所以这里不做 IP 白名单（用户明确要求不固定 IP）。
幂等：可重复执行。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.tc_api import call  # noqa: E402

INSTANCE = "lhins-f4zsj3q9"

DESIRED = [
    {"Protocol": "TCP", "Port": "443", "CidrBlock": "0.0.0.0/0",
     "Action": "ACCEPT", "FirewallRuleDescription": "AgentOS HTTPS"},
    {"Protocol": "TCP", "Port": "22", "CidrBlock": "0.0.0.0/0",
     "Action": "ACCEPT", "FirewallRuleDescription": "Linux SSH登录"},
    {"Protocol": "TCP", "Port": "3389", "CidrBlock": "0.0.0.0/0",
     "Action": "ACCEPT", "FirewallRuleDescription": "桌面远程登录 (xrdp)"},
    {"Protocol": "ICMP", "Port": "ALL", "CidrBlock": "0.0.0.0/0",
     "Action": "ACCEPT", "FirewallRuleDescription": "Ping 连通性"},
]


def main() -> int:
    current = call("DescribeFirewallRules", {"InstanceId": INSTANCE})
    version = current.get("FirewallVersion", 1)
    before = [(r["Protocol"], r["Port"], r.get("FirewallRuleDescription", ""))
              for r in current.get("FirewallRuleSet", [])]
    print("before:", before)
    print("version:", version)

    body = {
        "InstanceId": INSTANCE,
        "FirewallRules": DESIRED,
        "FirewallVersion": version,
    }
    result = call("ModifyFirewallRules", body)
    print("modified:", result.get("RequestId"))

    after = call("DescribeFirewallRules", {"InstanceId": INSTANCE})
    print("after:", [(r["Protocol"], r["Port"], r.get("FirewallRuleDescription", ""))
                     for r in after.get("FirewallRuleSet", [])])
    print("new version:", after.get("FirewallVersion"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
