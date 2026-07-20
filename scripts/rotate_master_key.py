#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    rotate_master_key.py
@brief   主密钥轮换脚本(H06-E10"轮换=迁移"):直接改 MASTER_KEY_HEX 会导致
         存量不可解,必须走本脚本——环境同时注入新钥(MASTER_KEY_HEX/
         MASTER_KEY_ID)与旧钥(OLD_MASTER_KEY_HEX/OLD_MASTER_KEY_ID),
         逐对象按原算法解包、当前主密钥重包;幂等、可断点、迁移前强制备份。
         用法:
           OLD_MASTER_KEY_HEX=<旧> OLD_MASTER_KEY_ID=mk1 \\
           MASTER_KEY_HEX=<新> MASTER_KEY_ID=mk2 \\
           python3 scripts/rotate_master_key.py --db-url sqlite:///data/idp.db \\
             --backup-dir backup/
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gd_common.jsonlog import get_logger  # noqa: E402
from gd_crypto.keyring import ENV_OLD_MASTER_KEY_HEX, MasterKeyRing  # noqa: E402
from gd_crypto.migrate import run_key_rotation  # noqa: E402
from gd_crypto.suites import current_suite  # noqa: E402
from gd_storage import apply_migrations, AuditWriter, Database  # noqa: E402
from scripts.migrate_crypto_suite import _backup_sqlite, SQLITE_PREFIX  # noqa: E402

_logger = get_logger("scripts.rotate_master_key")


def main() -> int:
    """@brief CLI 入口 @return 进程退出码"""
    parser = argparse.ArgumentParser(description="主密钥轮换(迁移式,H06-E10)")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--backup-dir", default="backup")
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--blob-dir", default=None)
    parser.add_argument("--i-have-backup", action="store_true")
    args = parser.parse_args()

    if not os.environ.get(ENV_OLD_MASTER_KEY_HEX):
        print("错误:轮换必须同时注入旧钥 OLD_MASTER_KEY_HEX(与 OLD_MASTER_KEY_ID),"
              "否则存量对象无法解包(H06-E10)。", file=sys.stderr)
        return 2
    if args.db_url.startswith(SQLITE_PREFIX):
        backup = _backup_sqlite(args.db_url, args.backup_dir)
        _logger.info("轮换前备份完成", extra={"backup": backup})
    elif not args.i_have_backup:
        print("错误:PostgreSQL 轮换前必须先 pg_dump 并携 --i-have-backup。",
              file=sys.stderr)
        return 2

    state_file = args.state_file or os.path.join(args.backup_dir,
                                                 "rotate_state.json")
    os.makedirs(args.backup_dir, exist_ok=True)
    db = Database(args.db_url)
    apply_migrations(db)
    ring = MasterKeyRing.from_env(os.environ)
    audit = AuditWriter(db, current_suite())
    report = run_key_rotation(db, ring, audit, blob_dir=args.blob_dir,
                              state_file=state_file)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("轮换完成:确认服务全部实例仅注入新钥后,方可移除 OLD_MASTER_KEY_HEX。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
