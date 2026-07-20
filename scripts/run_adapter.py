# -*- coding: utf-8 -*-
"""
@file    run_adapter.py
@brief   适配器启动入口:读 .env(M17 硬化解析,注释必须独立成行)→
         环境变量优先覆盖 → uvicorn 起 create_app 工厂(等价于
         `uvicorn apps.adapter.api.main:create_app --factory`)。
@usage   python3 scripts/run_adapter.py [--host 0.0.0.0] [--port 8000]
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))


def main() -> int:
    """@brief 装配配置并起服务"""
    parser = argparse.ArgumentParser(description="港电云云适配器")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--env-file", default=os.path.join(REPO_ROOT, ".env"))
    args = parser.parse_args()

    from apps.adapter.core.config import load_settings, parse_env_text
    env, warnings = {}, []
    if os.path.exists(args.env_file):
        with open(args.env_file, "r", encoding="utf-8") as handle:
            env, warnings = parse_env_text(handle.read())
    env.update(os.environ)                      # 环境变量优先(H00 G2)
    settings = load_settings(env, extra_warnings=warnings)
    for warning in settings.warnings:
        print(f"[env-warn] {warning}")

    import uvicorn
    from apps.adapter.api.main import create_app
    uvicorn.run(create_app(settings), host=args.host, port=args.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
