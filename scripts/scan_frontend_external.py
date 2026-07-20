# -*- coding: utf-8 -*-
"""
@file    scan_frontend_external.py
@brief   构建产物外部 URL 扫描(H09 §二 I.4 / H11 §七):CDN、外网字体、
         统计脚本零命中。只扫"承载性引用"(src=/href=/url(/import from/
         fetch()/new URL())以避免许可证注释里的说明性链接误报;
         另设域名黑名单直查全文(统计/字体/公共 CDN)。
@usage   python3 scripts/scan_frontend_external.py    # 命中即非零退出
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 承载性外链引用(实际会触发网络加载的语法位)
_LOAD_BEARING = re.compile(
    r"""(?:src|href)\s*=\s*["']https?://
      | url\(\s*["']?https?://
      | from\s+["']https?://
      | fetch\(\s*[`"']https?://
      | new\s+URL\(\s*[`"']https?://
      | @import\s+["']https?://""",
    re.X | re.I)

#: 域名黑名单(出现即违规,无论语法位;CDN/字体/统计)
_DOMAIN_BLACKLIST = (
    "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com",
    "fonts.googleapis.com", "fonts.gstatic.com", "fonts.font.im",
    "googletagmanager.com", "google-analytics.com", "hm.baidu.com",
    "cnzz.com", "umeng.com", "at.alicdn.com",
)

_SCAN_EXT = (".html", ".js", ".css", ".mjs")


def _iter_targets():
    """@brief 扫描对象:apps/**/web 全部前端产物与源(vendor license 除外)"""
    for base, _dirs, files in os.walk(os.path.join(ROOT, "apps")):
        if "__pycache__" in base:
            continue
        if os.sep + "web" not in base + os.sep:
            continue
        for name in files:
            if name.endswith(_SCAN_EXT):
                yield os.path.join(base, name)


def main() -> int:
    """@brief 入口"""
    hits, count = [], 0
    for path in _iter_targets():
        count += 1
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        rel = os.path.relpath(path, ROOT)
        for match in _LOAD_BEARING.finditer(text):
            hits.append(f"{rel}: 承载性外链 `{match.group(0)[:60]}`")
        for domain in _DOMAIN_BLACKLIST:
            if domain in text:
                hits.append(f"{rel}: 命中黑名单域 {domain}")
    if hits:
        for item in hits:
            print(f"[外链违规] {item}")
        return 1
    print(f"构建产物外部 URL 扫描:零命中({count} 个文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
