# -*- coding: utf-8 -*-
"""
@file    create_admin.py
@brief   管理员引导/重置脚本(H03 §7):生成一次性随机口令(仅本次输出一次)、
         强制首登改密、写审计;已存在账号执行口令重置语义。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:python scripts/create_admin.py --db sqlite:///data/platform.db --account boss
"""
import argparse
import os
import secrets
import string
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_crypto import hash_password                # noqa: E402
from gd_storage import events                      # noqa: E402
from apps.idp.context import IdpContext            # noqa: E402

ONE_TIME_PASSWORD_LENGTH = 16


def _one_time_password() -> str:
    """@brief 生成满足三类字符的一次性随机口令"""
    alphabet = string.ascii_letters + string.digits + "!@#%*"
    while True:
        candidate = "".join(secrets.choice(alphabet)
                            for _ in range(ONE_TIME_PASSWORD_LENGTH))
        has_upper = any(ch.isupper() for ch in candidate)
        has_lower = any(ch.islower() for ch in candidate)
        has_digit = any(ch.isdigit() for ch in candidate)
        if has_upper and has_lower and has_digit:
            return candidate


def main():
    """@brief 建号或重置管理员,一次性输出随机口令"""
    parser = argparse.ArgumentParser(description="管理员引导/重置(H03 §7)")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--key-dir", default=os.environ.get(
        "IDP_KEY_DIR", "data/keys"))
    parser.add_argument("--account", required=True)
    parser.add_argument("--display-name", default="平台管理员")
    args = parser.parse_args()

    ctx = IdpContext(args.db, args.key_dir)
    password = _one_time_password()
    try:
        existing = ctx.accounts.get_user(args.account)
        if existing is None:
            ctx.accounts.create_user(args.account, args.display_name, password,
                                     ctx.profile, "bootstrap-cli", "127.0.0.1",
                                     is_admin=True)
        else:
            ctx.db.execute(
                "UPDATE idp_users SET password_hash = ?, must_change_password = 1,"
                " is_admin = 1, status = 'active' WHERE account = ?",
                (hash_password(password, ctx.suite), args.account))
            ctx.audit.append("bootstrap-cli", events.PASSWORD_RESET,
                             {"account": args.account}, "127.0.0.1")
        # 口令仅本次输出(不入日志系统):首登强制改密后即作废
        print(f"账号: {args.account}")
        print(f"一次性口令(仅显示本次,首登强制修改): {password}")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
