# -*- coding: utf-8 -*-
"""
@file    check_frontend_e3.py
@brief   E3 双条款静态断言(06-E3 / H09 §二 I.1,CI 固化):
         条款一:内联脚本字面引用的元素 id 必须先于该 <script> 出现
                 (defer / type=module / 显式 DOMContentLoaded 包裹豁免);
         条款二:文档使用 hidden 属性时,可达 CSS 必须含
                 [hidden]{display:none} 兜底。
         检查对象:apps/**/web 下全部 .html、SPA dist(含 assets CSS)、
         factory3d 数据壳(真实渲染产物)。
@usage   python3 scripts/check_frontend_e3.py        # 违规即非零退出
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_ID_REF_RE = re.compile(r"""(?:getElementById|byId)\(\s*["']([^"']+)["']\s*\)""")
_HIDDEN_ATTR_RE = re.compile(r"<[a-z][^>]*\shidden[\s>/]", re.I)
_HIDDEN_CSS_RE = re.compile(r"\[hidden\]\s*\{[^}]*display\s*:\s*none", re.I)


def _collect_documents() -> list:
    """@brief 汇集待检 HTML 文档 @return [(名称, html 文本, 关联 css 文本)]"""
    docs = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "apps")):
        if "__pycache__" in base or os.sep + "vendor" in base:
            continue
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                html = fh.read()
            css = ""
            assets = os.path.join(os.path.dirname(path), "assets")
            if os.path.isdir(assets):          # SPA dist:外链 CSS 一并可达
                for asset in os.listdir(assets):
                    if asset.endswith(".css"):
                        with open(os.path.join(assets, asset),
                                  encoding="utf-8") as fh:
                            css += fh.read()
            docs.append((os.path.relpath(path, ROOT), html, css))
    # factory3d 数据壳:按真实渲染产物检查(nonce 不影响两条款)
    sys.path.insert(0, ROOT)
    from apps.factory3d.page import render_big_screen
    docs.append(("factory3d:/(rendered)",
                 render_big_screen("检查站点", "0.0.0", 12, nonce="e3check"), ""))
    return docs


def _check_clause_one(name: str, html: str) -> list:
    """@brief 条款一:脚本字面引用元素须先于 <script> 存在"""
    problems = []
    for match in _SCRIPT_RE.finditer(html):
        attrs, body = match.group(1), match.group(2)
        lowered = attrs.lower()
        if "src=" in lowered and ("defer" in lowered or "module" in lowered):
            continue                          # defer/module 天然延迟
        if "src=" in lowered and not body.strip():
            # 无 defer 的外链同步脚本无法静态看内容:要求 defer/module
            problems.append(f"{name}: 外链脚本未标 defer/module({attrs.strip()[:60]})")
            continue
        if "domcontentloaded" in body.lower():
            continue                          # 显式等待 DOM 就绪
        head = html[:match.start()]
        for ref in set(_ID_REF_RE.findall(body)):
            if f'id="{ref}"' not in head and f"id='{ref}'" not in head:
                problems.append(f"{name}: 脚本引用 #{ref} 早于元素定义(E3 条款一)")
    return problems


def _check_clause_two(name: str, html: str, css: str) -> list:
    """@brief 条款二:用 hidden 属性必有 [hidden]{display:none} 兜底"""
    if not _HIDDEN_ATTR_RE.search(html):
        return []
    reachable = html + css
    if _HIDDEN_CSS_RE.search(reachable):
        return []
    return [f"{name}: 使用 hidden 属性但缺 [hidden]{{display:none}} 兜底(E3 条款二)"]


def main() -> int:
    """@brief 入口:全部文档过两条款"""
    problems = []
    docs = _collect_documents()
    for name, html, css in docs:
        problems += _check_clause_one(name, html)
        problems += _check_clause_two(name, html, css)
    if problems:
        for item in problems:
            print(f"[E3 违规] {item}")
        return 1
    print(f"E3 双条款静态断言:通过({len(docs)} 份文档)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
