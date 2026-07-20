#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    selfcheck_prod.py
@brief   等保态开机自检(H05 §4):prod 模式启动与模式切换后自动执行,亦可 CLI 手动跑。
         逐项断言并输出报告;任何一项失败以非零码退出(fail-closed),
         MUST NOT 弱化为告警继续运行。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法: MASTER_KEY_HEX=... python3 scripts/selfcheck_prod.py [--db sqlite:///data/platform.db]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gd_common.errors import PlatformError                      # noqa: E402
from gd_crypto import (                                          # noqa: E402
    current_suite, decrypt_envelope, encrypt_envelope, MasterKeyRing,
)
from gd_policy import resolve_profile, SettingsService           # noqa: E402
from gd_storage import apply_migrations, Database, verify_chain  # noqa: E402
from selfcheck.registry import (                                 # noqa: E402
    PHASE_PROD, run_http_assertions, run_profile_assertions,
)

DEFAULT_DB_URL = "sqlite:///data/platform.db"
EXIT_OK = 0
EXIT_FAIL = 1
SELF_TEST_PLAINTEXT = b"selfcheck-crypto-roundtrip"


def _run_http_items(db_url: str, report: dict) -> list:
    """@brief 进程内构建 IdP 应用执行 D4/D5 端点断言(05 §4 / 13-R-IDP-3)"""
    import tempfile
    from apps.idp.context import IdpContext
    from apps.idp.web import create_app
    from selfcheck.asgi import AsgiClient
    ctx = IdpContext(db_url, key_dir=os.environ.get(
        "IDP_KEY_DIR", tempfile.mkdtemp(prefix="idp-keys-")))
    try:
        client = AsgiClient(create_app(ctx))
        results, failures = run_http_assertions(client, PHASE_PROD)
        report["http_items"] = results
        return [f"{f['id']} {f['name']}: 期望 {f['expected']} 实测 {f['observed']}"
                for f in failures]
    finally:
        ctx.close()


def _check_crypto_roundtrip(report: dict) -> list:
    """@brief 加解密回环自测 + 主密钥非演示派生校验(H05 §3.2.5 / §4)"""
    failures = []
    try:
        ring = MasterKeyRing.from_env()
        if ring.is_demo_key():
            failures.append("主密钥为演示派生默认值,禁止进入生产(H05 §3.2.5)")
        suite = current_suite()
        envelope = encrypt_envelope(SELF_TEST_PLAINTEXT, ring, suite)
        if decrypt_envelope(envelope, ring) != SELF_TEST_PLAINTEXT:
            failures.append("加解密回环自测结果不一致")
        report["crypto_suite"] = suite.name
    except PlatformError as exc:
        failures.append(f"密码学自检失败: {exc}")
    return failures


def main() -> int:
    """@brief 自检主流程 @return 进程退出码(0=全绿,非 0=fail-closed)"""
    parser = argparse.ArgumentParser(description="等保态开机自检(fail-closed)")
    parser.add_argument("--db", default=os.environ.get("GD_DB_URL", DEFAULT_DB_URL))
    args = parser.parse_args()

    report = {"db": args.db}
    failures = []
    db = Database(args.db)
    try:
        apply_migrations(db)
        settings = SettingsService(db)
        profile = resolve_profile(settings)
        report["mode"] = profile.mode
        if profile.is_demo:
            failures.append("当前为 DEMO 模式,生产自检要求 DEMO_MODE=0")
        results, item_failures, pending = run_profile_assertions(profile, PHASE_PROD)
        report["items"] = results
        report["pending"] = pending
        failures += [f"{f['id']} {f['name']}: 期望 {f['expected']} 实测 {f['observed']}"
                     for f in item_failures]
        failures += _run_http_items(args.db, report)   # D4/D5 端点断言(GAP-02 已解除)
        failures += _check_crypto_roundtrip(report)
        try:
            report["audit_chain_records"] = verify_chain(db)
        except PlatformError as exc:
            failures.append(f"审计链校验失败: {exc}")
    finally:
        db.close()

    report["failures"] = failures
    report["result"] = "PASS" if not failures else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK if not failures else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
