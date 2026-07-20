# -*- coding: utf-8 -*-
"""
@file    migrate_crypto_suite.py
@brief   密码套件官方迁移脚本(H04 §8.2.6 / 13-R-IDP-2):逐对象解旧包新,
         幂等 / 双写窗口兼容 / 断点续迁 / 审计锚点;迁移前强制备份
         (SQLite 自动副本;PostgreSQL 须先自行 pg_dump 并携 --i-have-backup)。
         用法示例:
           python3 scripts/migrate_crypto_suite.py --db-url sqlite:///data/idp.db \\
             --target gm --backup-dir backup/ --state-file backup/migrate_state.json
         中断后原命令重跑即自动续迁(结果与一次性迁移一致)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gd_common.jsonlog import get_logger  # noqa: E402
from gd_crypto.keyring import MasterKeyRing  # noqa: E402
from gd_crypto.migrate import run_migration  # noqa: E402
from gd_crypto.suites import get_suite, SUITE_GM, SUITE_INTL  # noqa: E402
from gd_storage import apply_migrations, AuditWriter, Database  # noqa: E402

_logger = get_logger("scripts.migrate_crypto_suite")
SQLITE_PREFIX = "sqlite:///"


def _backup_sqlite(db_url: str, backup_dir: str) -> str:
    """@brief SQLite 数据库文件强制备份 @return 备份文件路径"""
    db_path = db_url[len(SQLITE_PREFIX):]
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, os.path.basename(db_path) + ".pre-migrate.bak")
    shutil.copy2(db_path, dest)
    return dest


def main() -> int:
    """@brief CLI 入口 @return 进程退出码"""
    parser = argparse.ArgumentParser(description="密码套件迁移(intl⇄gm)")
    parser.add_argument("--db-url", required=True, help="目标库(可多次执行逐库迁移)")
    parser.add_argument("--target", required=True, choices=[SUITE_INTL, SUITE_GM])
    parser.add_argument("--backup-dir", default="backup", help="备份与断点目录")
    parser.add_argument("--state-file", default=None,
                        help="断点状态文件(缺省 <backup-dir>/migrate_state.json)")
    parser.add_argument("--blob-dir", default=None,
                        help="文件信封目录(certvault 数据目录,可选)")
    parser.add_argument("--i-have-backup", action="store_true",
                        help="PostgreSQL:确认已完成 pg_dump 备份(强制项)")
    args = parser.parse_args()

    if args.db_url.startswith(SQLITE_PREFIX):
        backup_path = _backup_sqlite(args.db_url, args.backup_dir)
        _logger.info("迁移前备份完成", extra={"backup": backup_path})
    elif not args.i_have_backup:
        # H04 §8.2.6:迁移前强制备份,人工不可跳过——PG 场景以显式确认承接
        print("错误:PostgreSQL 迁移前必须先完成 pg_dump 备份,"
              "并携带 --i-have-backup 显式确认。", file=sys.stderr)
        return 2

    state_file = args.state_file or os.path.join(args.backup_dir,
                                                 "migrate_state.json")
    os.makedirs(args.backup_dir, exist_ok=True)
    db = Database(args.db_url)
    apply_migrations(db)
    target = get_suite(args.target)
    audit = AuditWriter(db, target)
    ring = MasterKeyRing.from_env(os.environ)
    report = run_migration(db, ring, target, audit,
                           blob_dir=args.blob_dir, state_file=state_file)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
