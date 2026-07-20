#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    scan_secrets.py
@brief   敏感信息扫描(H06-P0-1 红线):打包/发版流水线内置,失败即中断且不可跳过;
         覆盖 .md 文件(P0-2);仓库只允许 .env.example 占位模板。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法: python3 scripts/scan_secrets.py [--path 仓库根] ; 命中即退出码 1
"""
import argparse
import os
import re
import sys

EXIT_CLEAN = 0
EXIT_HIT = 1
# 排除目录(第三方与产物;.env.example 为占位模板但仍参与扫描以校验其只含占位值)
EXCLUDED_DIRS = {".git", "node_modules", "wheels", "dist", "__pycache__", ".venv", "data"}
TEXT_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh", ".env",
                   ".example", ".cfg", ".ini", ".toml", ".sql", ".ts", ".tsx", ".js"}
MAX_FILE_BYTES = 2 * 1024 * 1024      # 超大文件跳过(非文本产物)
ALLOW_MARKER = "secretscan:allow"     # 行内显式豁免标记(须评审)

# 检测规则:名称 → 正则(覆盖遗留外泄形态:根密钥 hex、令牌、私钥、云 AK)
PATTERNS = {
    "PEM 私钥": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "64 位 hex 密钥赋值": re.compile(
        r"(?i)(master_key|key_hex|secret)[\w]*\s*[=:]\s*['\"]?[0-9a-f]{64}['\"]?"),
    "云访问密钥 AK": re.compile(r"\b(AKIA|LTAI)[0-9A-Za-z]{12,}\b"),
    "JWT 令牌": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."),
    "通用秘密赋值": re.compile(
        r"(?i)(password|passwd|secret|token|api_key)\s*[=:]\s*['\"][^'\"\s占位<{]{16,}['\"]"),
}
# 占位值白名单:.env.example 中允许的形态(H06-P0-1:仓库只允许占位模板)
PLACEHOLDER_RE = re.compile(r"(CHANGE_ME|占位|<[^>]+>|\.\.\.|xxx+|见密钥保管处)", re.IGNORECASE)


def _iter_text_files(root: str):
    """@brief 遍历待扫描文本文件"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXTENSIONS or name.startswith(".env"):
                if os.path.getsize(path) <= MAX_FILE_BYTES:
                    yield path


def scan_tree(root: str) -> list:
    """
    @brief  扫描目录树,返回命中清单
    @param  root 仓库根目录
    @return [(文件, 行号, 规则名, 行摘要)] 列表
    """
    findings = []
    for path in _iter_text_files(root):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if ALLOW_MARKER in line or PLACEHOLDER_RE.search(line):
                continue
            for rule_name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((os.path.relpath(path, root), line_no,
                                     rule_name, line.strip()[:120]))
    return findings


def main() -> int:
    """@brief CLI 入口 @return 0=零命中,1=命中(流水线必须中断)"""
    parser = argparse.ArgumentParser(description="敏感信息扫描(P0-1,不可跳过)")
    parser.add_argument("--path", default=os.path.join(os.path.dirname(__file__), ".."))
    args = parser.parse_args()
    findings = scan_tree(os.path.abspath(args.path))
    if not findings:
        print("敏感信息扫描:零命中")
        return EXIT_CLEAN
    print(f"敏感信息扫描:命中 {len(findings)} 处,发版流水线必须中断(H06-P0-1):")
    for path, line_no, rule_name, snippet in findings:
        print(f"  [{rule_name}] {path}:{line_no}  {snippet}")
    return EXIT_HIT


if __name__ == "__main__":
    sys.exit(main())
