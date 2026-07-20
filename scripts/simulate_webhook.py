# -*- coding: utf-8 -*-
"""
@file    simulate_webhook.py
@brief   模拟厂商推送(L01 §11):经服务端同款验签器进程内闭环测试;
         --bad-sig 验证 401 分支;--dry-run 输出等效 curl 不实际请求。
@usage   python3 scripts/simulate_webhook.py {zhiguang|siyun} [--bad-sig] [--dry-run]
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))

SAMPLE_ZHIGUANG = {"id": 20260720, "status": 1, "robotSn": "ZG-DEMO-01",
                   "alarmType": "brush_stuck", "level": 2,
                   "time": "2026-07-20 09:00:00"}
SAMPLE_SIYUN = {"event_type": "flycart_status", "sub_type": "landed",
                "id": "evt-demo-01", "deviceSn": "FC-DEMO-01",
                "time": "2026-07-20 09:00:00"}


def build_request(vendor: str, bad_sig: bool, settings) -> tuple:
    """@brief 组装 (path, headers, body):签名用服务端同款算法"""
    if vendor == "zhiguang":
        from apps.adapter.core.vendors.zhiguang import ZhiguangSigner
        body = json.dumps(SAMPLE_ZHIGUANG, ensure_ascii=False).encode("utf-8")
        signer = ZhiguangSigner(settings.zg_app_secret, settings.zg_sign_mode)
        signature = "bad-signature" if bad_sig else signer.sign(body)
        return ("/api/v1/webhooks/zhiguang",
                {"Content-Type": "application/json",
                 "X-ZG-Signature": signature}, body)
    from apps.adapter.core.vendors.siyun import td022_signature
    body = json.dumps(SAMPLE_SIYUN, ensure_ascii=False).encode("utf-8")
    ts, nonce = str(int(time.time())), "demo-nonce"
    signature = "bad-signature" if bad_sig else td022_signature(
        settings.siyun_ak, settings.siyun_sk, ts, nonce,
        SAMPLE_SIYUN["event_type"], SAMPLE_SIYUN["sub_type"])
    return ("/api/v1/webhooks/siyun",
            {"Content-Type": "application/json", "X-DJI-Signature": signature,
             "X-DJI-Timestamp": ts, "X-DJI-Nonce": nonce}, body)


def as_curl(path: str, headers: dict, body: bytes) -> str:
    """@brief 等效 curl(--dry-run)"""
    parts = [f"curl -X POST 'http://127.0.0.1:8000{path}'"]
    for name, value in headers.items():
        parts.append(f"  -H '{name}: {value}'")
    parts.append(f"  -d '{body.decode('utf-8')}'")
    return " \\\n".join(parts)


def main() -> int:
    """@brief 入口:进程内起应用走完整验签/翻译/入汇链路"""
    args = sys.argv[1:]
    vendor = args[0] if args and not args[0].startswith("--") else "zhiguang"
    if vendor not in ("zhiguang", "siyun"):
        print(__doc__)
        return 2
    bad_sig = "--bad-sig" in args
    dry_run = "--dry-run" in args

    from apps.adapter.core.config import Settings, load_settings
    settings = load_settings(dict(os.environ))
    if not settings.zg_app_secret:
        settings.zg_app_secret = "demo-secret"
    if not settings.siyun_ak:
        settings.siyun_ak, settings.siyun_sk = "demo-ak", "demo-sk"
    path, headers, body = build_request(vendor, bad_sig, settings)
    if dry_run:
        print(as_curl(path, headers, body))
        return 0
    from apps.adapter.api.main import create_app
    from selfcheck.asgi import AsgiClient
    client = AsgiClient(create_app(settings))
    resp = client.request("POST", path, raw_body=body,
                          headers={k: v for k, v in headers.items()
                                   if k != "Content-Type"},
                          content_type="application/json")
    print(f"HTTP {resp.status_code}  X-Request-Id: "
          f"{resp.headers.get('x-request-id', '-')}")
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    expected = 401 if bad_sig else 200
    return 0 if resp.status_code == expected else 1


if __name__ == "__main__":
    sys.exit(main())
