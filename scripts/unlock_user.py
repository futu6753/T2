# -*- coding: utf-8 -*-
"""
@file    unlock_user.py
@brief   官方解锁 CLI(H03 §7 / 06-E5):容器内执行,仅清除锁定标记不改口令,
         写审计;替代遗留"手写 SQL"自救方式。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:python scripts/unlock_user.py --db sqlite:///data/platform.db --account boss
"""
import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.idp.context import IdpContext            # noqa: E402


def main():
    """@brief 解析参数并执行解锁(留审计 user_unlocked)"""
    parser = argparse.ArgumentParser(description="管理员自锁自救(H03 §7)")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--key-dir", default=os.environ.get(
        "IDP_KEY_DIR", "data/keys"))
    parser.add_argument("--account", required=True)
    args = parser.parse_args()

    ctx = IdpContext(args.db, args.key_dir)
    try:
        ctx.accounts.admin_unlock(args.account, "unlock-cli", "127.0.0.1")
        print(f"已解锁: {args.account}(仅清除锁定标记,口令未变更)")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
