#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    run_rp.py
@brief   四个 RP 的统一生产启动入口(里程碑 10 部署包):
         `python3 scripts/run_rp.py certvault --port 9100`。装配 Database/
         MasterKeyRing/套件/SsoClient(HTTP 传输)/易失态(REDIS_URL 有则
         Redis,fail-closed;否则本地单机),再交 uvicorn 托管。
         SSO 四变量(SSO_ISSUER/CLIENT_ID/CLIENT_SECRET/REDIRECT)缺任一
         则 SSO 不启用(certvault 本地登录仍可用)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import os
import sys
import urllib.request

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_common.errors import PlatformError                      # noqa: E402
from gd_crypto.keyring import MasterKeyRing                     # noqa: E402
from gd_crypto.suites import current_suite                      # noqa: E402
from gd_sso_client.client import load_config, SsoClient         # noqa: E402
from gd_storage import (                                        # noqa: E402
    apply_migrations, Database, LocalVolatileStore, RedisVolatileStore,
)

DEFAULT_PORTS = {"certvault": 9100, "nvr": 9200, "factory3d": 9300,
                 "quiz": 9400}
HTTP_TIMEOUT_SECONDS = 10


def _http_transport(method: str, url: str, headers: dict, body):
    """@brief SsoClient 生产传输层(urllib)@return (status, headers, body)"""
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request,
                                    timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (urllib.error.URLError, OSError) as exc:
        # DNS 解析失败/证书校验失败/连接拒绝等一律转 PlatformError:
        # RP 路由层捕获后回 503 + 明文原因,而非裸 500(06-E18)
        reason = getattr(exc, "reason", None) or exc
        raise PlatformError(
            f"SSO 后端不可达 {url.split('?')[0]}: {reason}") from exc


def _make_store():
    """@brief 易失态装配:REDIS_URL 有则 Redis(fail-closed),否则本地单机"""
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return LocalVolatileStore()
    import redis
    return RedisVolatileStore(redis.Redis.from_url(redis_url))


def _make_sso(app_name: str, store):
    """@brief 装配 SsoClient(四变量缺任一 → 不启用,返回禁用实例)"""
    return SsoClient(load_config(os.environ), store, _http_transport,
                     system=app_name)


def build_app(app_name: str, db, ring, suite, store, sso):
    """@brief 按应用名装配 FastAPI 实例(工厂签名差异集中在此)"""
    if app_name == "certvault":
        from apps.certvault.web import create_app
        return create_app(db, ring, suite, store, sso,
                          blob_dir=os.environ.get("CV_BLOB_DIR", "data/blobs"))
    if app_name == "nvr":
        from apps.nvr.web import create_app
        return create_app(db, suite, sso, ring=ring)
    if app_name == "factory3d":
        from apps.factory3d.web import create_app
        return create_app(db, suite, sso,
                          admin_token=os.environ.get("F3D_ADMIN_TOKEN", ""),
                          ring=ring)
    if app_name == "quiz":
        from apps.quiz.web import create_app
        return create_app(db, suite, sso)
    raise SystemExit(f"未知应用: {app_name}")


def main():
    """@brief 解析参数、装配依赖、uvicorn 托管"""
    parser = argparse.ArgumentParser(description="港电 RP 统一启动入口")
    parser.add_argument("app", choices=sorted(DEFAULT_PORTS))
    parser.add_argument("--db", default=os.environ.get(
        "DATABASE_URL", "sqlite:///data/platform.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    db = Database(args.db)
    apply_migrations(db)
    ring = MasterKeyRing.from_env(os.environ)
    suite = current_suite()
    store = _make_store()
    app = build_app(args.app, db, ring, suite, store,
                    _make_sso(args.app, store))
    import uvicorn
    uvicorn.run(app, host=args.host,
                port=args.port or DEFAULT_PORTS[args.app], log_level="info")


if __name__ == "__main__":
    main()
