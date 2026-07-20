#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    make_packs.py
@brief   三包流水线 CLI(H10):`make copyright-pack|paper-pack|patent-pack`
         的实际入口;支持 --system 单系统或 all。
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

from scripts.packs.generator import build  # noqa: E402
from scripts.packs.systems import SYSTEMS  # noqa: E402


def main() -> int:
    """@brief 解析参数并生成指定包 @return 退出码"""
    parser = argparse.ArgumentParser(description="三包流水线(H10)")
    parser.add_argument("kind", choices=["copyright", "paper", "patent", "all"])
    parser.add_argument("--system", default="all",
                        choices=["all"] + list(SYSTEMS))
    args = parser.parse_args()
    kinds = ["copyright", "paper", "patent"] if args.kind == "all" else [args.kind]
    for kind in kinds:
        for out_dir in build(kind, args.system):
            print(f"[{kind}] {os.path.relpath(out_dir, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
