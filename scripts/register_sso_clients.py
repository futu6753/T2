#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    register_sso_clients.py
@brief   六 RP 的 OIDC 客户端一键登记(部署引导):向 IdP 目录登记
         certvault/nvr/factory3d/quiz 四个走 SSO 的客户端(backchannel 扇出
         登出用),生成一次性明文 client_secret 并打印为 .env 片段;已存在则
         跳过(幂等,不重置密钥,避免误改线上)。adapter 不走 SSO,不登记。
         须与 IdP 连同一 DATABASE_URL / MASTER_KEY_HEX 环境运行。
         用法(在 idp 容器内或同环境宿主机):
           python3 scripts/register_sso_clients.py --base-domain gangdian.internal
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

from apps.idp.context import IdpContext            # noqa: E402

# RP → (client_id, 子域, .env 密钥变量名)
RP_CLIENTS = (
    ("certvault", "cv", "CV_SSO_SECRET"),
    ("nvr", "nvr", "NVR_SSO_SECRET"),
    ("factory3d", "f3d", "F3D_SSO_SECRET"),
    ("quiz", "quiz", "QUIZ_SSO_SECRET"),
)


def main() -> int:
    """@brief 登记四 RP 客户端并输出 .env 片段 @return 退出码"""
    parser = argparse.ArgumentParser(description="六 RP OIDC 客户端一键登记")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--key-dir", default=os.environ.get(
        "IDP_KEY_DIR", "data/keys"))
    parser.add_argument("--base-domain", default="gangdian.internal")
    parser.add_argument("--scheme", default="https")
    args = parser.parse_args()

    ctx = IdpContext(args.db, args.key_dir)
    env_lines, skipped = [], []
    try:
        for client_id, sub, env_var in RP_CLIENTS:
            base = f"{args.scheme}://{sub}.{args.base_domain}"
            if ctx.oidc.get_client(client_id) is not None:
                skipped.append(client_id)
                continue
            secret = ctx.oidc.create_client(
                client_id, f"{client_id} 系统",
                [f"{base}/sso/callback"],
                backchannel_url=f"{base}/backchannel-logout")
            env_lines.append(f"{env_var}={secret}")
    finally:
        ctx.close()

    if skipped:
        print(f"# 已存在,跳过(未重置密钥):{', '.join(skipped)}", file=sys.stderr)
    if env_lines:
        print("# ↓↓↓ 将以下行写入 .env(一次性明文,库中仅存哈希)↓↓↓")
        print("\n".join(env_lines))
    else:
        print("# 全部客户端已登记;如需重置密钥请先在管理台删除对应客户端。",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
