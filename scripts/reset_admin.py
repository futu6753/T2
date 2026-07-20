#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    reset_admin.py
@brief   管理员口令重置自救(交付物清单 4 / H03 §7):生成一次性临时口令、
         清除锁定、置首登强改标志;全程留审计。与 unlock_user(仅清锁)、
         create_admin(建号)构成三件套。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import os
import secrets
import string
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.idp.context import IdpContext            # noqa: E402
from gd_crypto.password import hash_password       # noqa: E402

TEMP_PASSWORD_LEN = 16
# 口令字符池:保证四类字符齐备(H03 复杂度基线)
_POOLS = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#%*")


def _gen_temp_password() -> str:
    """@brief 生成满足复杂度的一次性临时口令"""
    chars = [secrets.choice(pool) for pool in _POOLS]
    chars += [secrets.choice("".join(_POOLS))
              for _ in range(TEMP_PASSWORD_LEN - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def main() -> int:
    """@brief 重置指定管理员口令(临时口令仅显示一次)@return 退出码"""
    parser = argparse.ArgumentParser(description="管理员口令重置(H03 §7 自救)")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--key-dir", default=os.environ.get(
        "IDP_KEY_DIR", "data/keys"))
    parser.add_argument("--account", required=True)
    args = parser.parse_args()

    ctx = IdpContext(args.db, args.key_dir)
    try:
        user = ctx.accounts.get_user(args.account)
        if user is None:
            print(f"错误:账号 {args.account} 不存在", file=sys.stderr)
            return 2
        temp_password = _gen_temp_password()
        ctx.db.execute(
            "UPDATE idp_users SET password_hash = ?, must_change_password = 1"
            " WHERE account = ?",
            (hash_password(temp_password, ctx.suite), args.account))
        ctx.accounts.admin_unlock(args.account, "reset-admin-cli", "127.0.0.1")
        print(f"已重置 {args.account} 口令(仅显示一次,首次登录强制改密):")
        print(f"  临时口令: {temp_password}")
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
