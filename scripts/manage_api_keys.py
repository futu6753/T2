# -*- coding: utf-8 -*-
"""
@file    manage_api_keys.py
@brief   对外只读 API 密钥管理(L04 §8):create(明文仅显示一次,密文
         信封加密落库)/list/revoke。需 MASTER_KEY_HEX 环境变量。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:MASTER_KEY_HEX=… python3 scripts/manage_api_keys.py \
        --db sqlite:///data/platform.db create --key-id partner-a
"""
import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_crypto import MasterKeyRing                       # noqa: E402
from gd_crypto.suites import get_suite                    # noqa: E402
from gd_storage import Database, apply_migrations         # noqa: E402
from apps.nvr.exposition import PublicApiGuard            # noqa: E402


def main() -> int:
    """@brief CLI 入口"""
    parser = argparse.ArgumentParser(description="对外 API 密钥管理(L04 §8)")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--key-id", required=True)
    sub.add_parser("list")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--key-id", required=True)
    args = parser.parse_args()

    ring = MasterKeyRing.from_env(os.environ)
    db = Database(args.db)
    apply_migrations(db)
    guard = PublicApiGuard(db, ring, get_suite("intl"))
    if args.command == "create":
        secret = guard.create_key(args.key_id)
        print(f"key_id : {args.key_id}")
        print(f"secret : {secret}")
        print("以上明文仅显示本次,密文已信封加密落库;请立即安全交付对端。")
    elif args.command == "list":
        rows = db.query(
            "SELECT key_id, created_at, revoked_at FROM nvr_api_keys"
            " ORDER BY id")
        for key_id, created_at, revoked_at in rows:
            state = "已吊销" if revoked_at else "有效"
            print(f"{key_id:<24}{state:<8}建于 {created_at[:19]}")
        if not rows:
            print("(空)")
    else:
        guard.revoke_key(args.key_id)
        print(f"已吊销: {args.key_id}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
