# -*- coding: utf-8 -*-
"""
@file    run_idp.py
@brief   IdP 启动入口:DEMO 环境检查(0.0.0.0+DEMO 阻止,H05 §2)、
         生产开机自检 fail-closed(H05 §4)、DEMO 态每小时审计心跳
         (H05 §2,GAP-07 解除)、uvicorn 托管。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:python scripts/run_idp.py --db sqlite:///data/platform.db \
        --key-dir data/keys --host 127.0.0.1 --port 9000
"""
import argparse
import asyncio
import contextlib
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_common.jsonlog import get_logger                       # noqa: E402
from gd_policy.profile import check_bind_allowed               # noqa: E402
from gd_storage import events                                  # noqa: E402
from apps.idp.context import IdpContext                        # noqa: E402
from apps.idp.mode import ModeService                          # noqa: E402
from apps.idp.web import create_app                            # noqa: E402
from selfcheck.asgi import AsgiClient                          # noqa: E402
from selfcheck.registry import (                               # noqa: E402
    PHASE_PROD, run_http_assertions, run_profile_assertions,
)

_log = get_logger("idp.run")
DEMO_HEARTBEAT_SECONDS = 3600      # DEMO 审计心跳周期(线上误开可追溯时长)


def _startup_selfcheck(ctx):
    """@brief 生产开机自检:profile 级 + http 级,失败即拒绝启动(fail-closed)"""
    _, failures, _ = run_profile_assertions(ctx.profile, PHASE_PROD)
    client = AsgiClient(create_app(ctx))
    _, http_failures = run_http_assertions(client, PHASE_PROD)
    failed = failures + http_failures
    if failed:
        _log.error("开机自检失败,拒绝启动",
                   extra={"ctx": {"items": [f["id"] for f in failed]}})
        raise SystemExit(1)
    _log.info("生产开机自检全绿")


async def _demo_heartbeat_task(ctx):
    """@brief DEMO 态每小时写一条审计心跳(H05 §2)"""
    while True:
        ctx.audit.append("system", events.MODE_DEMO_HEARTBEAT,
                         {"mode": ctx.profile.mode}, "127.0.0.1")
        await asyncio.sleep(DEMO_HEARTBEAT_SECONDS)


def main():
    """@brief 解析参数、执行环境检查与自检、托管服务"""
    parser = argparse.ArgumentParser(description="港电统一认证中心启动入口")
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--key-dir", default=os.environ.get(
        "IDP_KEY_DIR", "data/keys"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    ctx = IdpContext(args.db, args.key_dir)
    check_bind_allowed(args.host, ctx.profile)      # 0.0.0.0+DEMO 阻止(H05 §2)
    if not ctx.profile.is_demo:
        ModeService(ctx)._check_prod_preconditions()
        _startup_selfcheck(ctx)
    else:
        ctx.accounts.seed_demo_accounts(ctx.profile, "127.0.0.1")
    app = create_app(ctx)

    import uvicorn

    async def serve():
        """@brief 事件循环内并行:HTTP 服务 + DEMO 心跳(挂应用生命周期,L1-12)"""
        heartbeat = None
        if ctx.profile.is_demo:
            heartbeat = asyncio.get_running_loop().create_task(
                _demo_heartbeat_task(ctx))
        config = uvicorn.Config(app, host=args.host, port=args.port,
                                log_level="info")
        try:
            await uvicorn.Server(config).serve()
        finally:
            if heartbeat:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    asyncio.run(serve())


if __name__ == "__main__":
    main()
