#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    check_demo_mode_usage.py
@brief   CI 静态检查(H05 ai_directives):业务代码不得直接读取 DEMO_MODE,
         白名单仅 SecurityProfile 解析器/schema、自检与脚本、测试。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import re
import sys

TARGET_DIRS = ("packages", "apps")
WHITELIST_SUFFIXES = (
    os.path.join("gd_policy", "profile.py"),
    os.path.join("gd_policy", "schema.py"),
)
PATTERN = re.compile(r"\bDEMO_MODE\b")
EXIT_CLEAN = 0
EXIT_VIOLATION = 1


def scan(root: str) -> list:
    """@brief 扫描业务代码中的 DEMO_MODE 直接引用 @return 违规 (文件,行号) 列表"""
    violations = []
    for base in TARGET_DIRS:
        base_path = os.path.join(root, base)
        if not os.path.isdir(base_path):
            continue
        for dirpath, _, filenames in os.walk(base_path):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                if any(path.endswith(suffix) for suffix in WHITELIST_SUFFIXES):
                    continue
                with open(path, "r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        if PATTERN.search(line):
                            violations.append((os.path.relpath(path, root), line_no))
    return violations


def main() -> int:
    """@brief CLI 入口"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    violations = scan(root)
    if not violations:
        print("DEMO_MODE 单一入口检查:通过")
        return EXIT_CLEAN
    print("业务代码禁止直接判 DEMO_MODE(唯一入口为 SecurityProfile,H05 §1.2):")
    for path, line_no in violations:
        print(f"  {path}:{line_no}")
    return EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())
