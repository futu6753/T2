#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    set_rp_role.py
@brief   RP 本地账户角色调整(运维口)。SSO 首登自动建号一律最小角色
         (H03 §1),而各 RP 的管理界面又要求 admin,首个管理员无提权
         入口(鸡生蛋)。本脚本为部署侧出口:直接调 RpAccountService.set_role
         改本地角色,角色子集校验由服务层保证。
@usage   容器内执行(DATABASE_URL 已注入):
           docker compose -f deploy/docker-compose.single.yml exec nvr \
             python3 scripts/set_rp_role.py nvr --username op_admin --role admin
         先 --list 查看已建号账户(账户须先 SSO 登录过一次该系统):
           python3 scripts/set_rp_role.py nvr --list
@author  港电实验室平台组
@date    2026-07-21
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_crypto.suites import current_suite                      # noqa: E402
from gd_storage import apply_migrations, Database               # noqa: E402
from apps.rp_common.accounts import RpAccountService            # noqa: E402

# 各 RP 账户表与角色子集(与 apps/*/web.py 装配处保持一致)
RP_ACCOUNTS = {
    "certvault": dict(table="cv_users", roles=("admin", "user"),
                      default="user", tva=True),
    "nvr":       dict(table="nvr_users", roles=("admin", "operator", "auditor"),
                      default="auditor", tva=False),
    "factory3d": dict(table="f3d_users", roles=("admin", "operator"),
                      default="operator", tva=False),
    "quiz":      dict(table="quiz_users", roles=("admin", "user"),
                      default="user", tva=False),
}


def _service(app_name: str, db: Database) -> RpAccountService:
    """@brief 按应用名装配账户服务(表名/角色子集与线上一致)"""
    spec = RP_ACCOUNTS[app_name]
    return RpAccountService(db, current_suite(), table=spec["table"],
                            allowed_roles=spec["roles"],
                            default_role=spec["default"],
                            has_token_valid_after=spec["tva"])


def _list_users(app_name: str, db: Database):
    """@brief 列出该系统已建号账户(username/role/status)"""
    table = RP_ACCOUNTS[app_name]["table"]
    rows = db.query(f"SELECT username, display_name, role, status"
                    f" FROM {table} ORDER BY username")
    if not rows:
        print(f"[{app_name}] 账户表为空:账户须先 SSO 登录一次该系统"
              f"(首登自动建号)后再提权")
        return
    print(f"[{app_name}] 共 {len(rows)} 个账户:")
    for username, display, role, status in rows:
        print(f"  {username:<24} 显示名={display:<16} 角色={role:<10}"
              f" 状态={status}")


def main():
    """@brief 参数解析与执行"""
    parser = argparse.ArgumentParser(description="RP 本地账户角色调整")
    parser.add_argument("app", choices=sorted(RP_ACCOUNTS))
    parser.add_argument("--username", help="RP 本地用户名(SSO 建号=IdP 账户名)")
    parser.add_argument("--role", help="目标角色(须在本系统角色子集内)")
    parser.add_argument("--list", action="store_true", help="仅列出账户")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    args = parser.parse_args()

    db = Database(args.db)
    apply_migrations(db)

    if args.list:
        _list_users(args.app, db)
        return
    if not args.username or not args.role:
        parser.error("非 --list 模式必须提供 --username 与 --role")

    service = _service(args.app, db)
    user = service.get_by_username(args.username)
    if user is None:
        print(f"[{args.app}] 账户 {args.username} 不存在。"
              f"提示:SSO 账户须先登录一次该系统(首登自动建号,用户名=IdP 账户名);"
              f"用 --list 查看已建号账户。")
        sys.exit(1)
    before = user["role"]
    service.set_role(args.username, args.role)   # 角色子集校验在服务层
    print(f"[{args.app}] {args.username}: {before} → {args.role}")


if __name__ == "__main__":
    main()
