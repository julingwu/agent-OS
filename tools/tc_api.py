"""腾讯云 API 3.0 最小客户端（仅标准库，无第三方依赖）。

用途：服务器重装/防火墙/实例查询等运维动作，避免 tccli 在 PowerShell 下的 JSON 转义问题。

用法：
    python tools/tc_api.py DescribeInstances --region ap-guangzhou --body '{}'
    python tools/tc_api.py ResetInstance --region ap-guangzhou --body-file req.json

凭据：默认从 ~/Downloads/SecretKey*.csv 读取，也可用环境变量
TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 覆盖。
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
import urllib.request
from datetime import datetime, timezone

SERVICE = "lighthouse"
HOST = "lighthouse.tencentcloudapi.com"
VERSION = "2020-03-24"
ALGORITHM = "TC3-HMAC-SHA256"


def load_credentials(csv_path: str | None = None) -> tuple[str, str]:
    sid = os.environ.get("TENCENTCLOUD_SECRET_ID")
    skey = os.environ.get("TENCENTCLOUD_SECRET_KEY")
    if sid and skey:
        return sid, skey
    if csv_path is None:
        candidates = sorted(glob.glob(str(pathlib.Path.home() / "Downloads" / "SecretKey*.csv")))
        if not candidates:
            raise SystemExit("no credentials: set env vars or pass --secrets <csv>")
        csv_path = candidates[-1]
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        row = next(csv.DictReader(fh))
    return row["SecretId"].strip(), row["SecretKey"].strip()


def _sign(secret_id: str, secret_key: str, action: str, region: str,
          payload: dict, ts: int | None = None) -> tuple[dict, str]:
    timestamp = ts if ts is not None else int(time.time())
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{HOST}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, signed_headers, hashed_payload,
    ])
    credential_scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        ALGORITHM, str(timestamp), credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, SERVICE)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    headers = {
        "Authorization": (
            f"{ALGORITHM} Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Content-Type": content_type,
        "Host": HOST,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": VERSION,
    }
    if region:
        headers["X-TC-Region"] = region
    return headers, body


def call(action: str, payload: dict | None = None, *, region: str = "ap-guangzhou",
         secrets: str | None = None, timeout: int = 30) -> dict:
    """调用一次 Lighthouse API，返回 Response 字典（含 RequestId）。"""
    secret_id, secret_key = load_credentials(secrets)
    headers, body = _sign(secret_id, secret_key, action, region, payload or {})
    req = urllib.request.Request(
        f"https://{HOST}", data=body.encode("utf-8"), headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    result = data.get("Response", {})
    if "Error" in result:
        raise RuntimeError(
            f"{action} failed: {result['Error'].get('Code')} "
            f"{result['Error'].get('Message')}"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tencent Lighthouse API client")
    ap.add_argument("action")
    ap.add_argument("--region", default="ap-guangzhou")
    ap.add_argument("--body", default="{}", help="request body as JSON string")
    ap.add_argument("--body-file", help="read request body from JSON file")
    ap.add_argument("--secrets", help="path to SecretKey csv")
    args = ap.parse_args(argv)

    if args.body_file:
        # Windows PowerShell 的 Out-File utf8 会带 BOM，这里统一用 utf-8-sig 读
        payload = json.loads(pathlib.Path(args.body_file).read_text(encoding="utf-8-sig"))
    else:
        payload = json.loads(args.body)
    try:
        result = call(args.action, payload, region=args.region, secrets=args.secrets)
    except Exception as exc:  # noqa: BLE001 - CLI 直接报错即可
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
