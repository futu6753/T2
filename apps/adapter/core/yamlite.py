# -*- coding: utf-8 -*-
"""
@file    yamlite.py
@brief   受限 YAML 子集解析器(自实现,纯标准库):映射 DSL 文件
         (harness/mappings/*.yaml)是我方契约工件,语法收敛为:
         两空格缩进的块级映射/列表、标量(引号串/整数/浮点/布尔/null)、
         整行 # 注释。不支持锚点/别名/流式/多行标量/行内注释——
         core 零第三方依赖(L01 §2),不得引入 pyyaml。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""

INDENT_UNIT = 2


class YamliteError(ValueError):
    """DSL 文件语法错误(带行号的人话报错)。"""


def _scalar(text: str):
    """@brief 标量解析:引号串/布尔/null/整数/浮点/普通串"""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _lines(text: str) -> list:
    """@brief 预处理:去空行/整行注释,产出 (行号, 缩进, 内容)"""
    out = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise YamliteError(f"第 {lineno} 行:缩进禁用制表符")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % INDENT_UNIT:
            raise YamliteError(f"第 {lineno} 行:缩进必须为 2 空格的倍数")
        out.append((lineno, indent, stripped))
    return out


def _split_key(content: str, lineno: int) -> tuple:
    """@brief 拆 key: value(key 可带引号;值内冒号不受影响)"""
    if content.startswith(("\"", "'")):
        quote = content[0]
        end = content.find(quote, 1)
        remainder = content[end + 1:].lstrip() if end > 0 else ""
        if end < 0 or not remainder.startswith(":"):
            raise YamliteError(f"第 {lineno} 行:引号键格式非法")
        return content[1:end], remainder[1:].strip()
    head, sep, rest = content.partition(":")
    if not sep or " " in head.strip():
        raise YamliteError(f"第 {lineno} 行:期望 key: value")
    return head.strip(), rest.strip()


def _looks_like_map_item(body: str) -> bool:
    """@brief 判断 "- xxx" 的 xxx 是否为映射起头(key: 或 key: value)"""
    if body.startswith(("\"", "'")):
        quote = body[0]
        end = body.find(quote, 1)
        return end > 0 and body[end + 1:].lstrip().startswith(":")
    head, sep, rest = body.partition(":")
    return bool(sep) and " " not in head.strip() and (
        rest == "" or rest.startswith(" "))


def _parse_block(lines: list, pos: int, indent: int):
    """@brief 递归解析同缩进块 @return (值, 下一位置)"""
    lineno, _, content = lines[pos]
    if content.startswith("- "):
        return _parse_list(lines, pos, indent)
    return _parse_map(lines, pos, indent)


def _parse_map(lines: list, pos: int, indent: int):
    """@brief 解析块级映射"""
    result = {}
    while pos < len(lines):
        lineno, line_indent, content = lines[pos]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise YamliteError(f"第 {lineno} 行:缩进过深")
        if content.startswith("- "):
            raise YamliteError(f"第 {lineno} 行:映射块内出现列表项")
        key, rest = _split_key(content, lineno)
        if key in result:
            raise YamliteError(f"第 {lineno} 行:键 {key} 重复")
        pos += 1
        if rest:
            result[key] = _scalar(rest)
        elif pos < len(lines) and lines[pos][1] > indent:
            result[key], pos = _parse_block(lines, pos, lines[pos][1])
        else:
            result[key] = None
    return result, pos


def _parse_list(lines: list, pos: int, indent: int):
    """@brief 解析块级列表(支持 "- key: value" 起头的映射项)"""
    result = []
    while pos < len(lines):
        lineno, line_indent, content = lines[pos]
        if line_indent != indent or not content.startswith("- "):
            if line_indent >= indent:
                raise YamliteError(f"第 {lineno} 行:列表块内出现非列表项")
            break
        body = content[2:].strip()
        pos += 1
        if not body:
            item, pos = _parse_block(lines, pos, indent + INDENT_UNIT)
            result.append(item)
            continue
        if _looks_like_map_item(body):
            # "- key: value" 内联起头的映射项,后续更深缩进行并入同一映射
            key, rest = _split_key(body, lineno)
            if rest:
                item = {key: _scalar(rest)}
            elif pos < len(lines) and lines[pos][1] > indent + INDENT_UNIT:
                nested, pos = _parse_block(lines, pos, lines[pos][1])
                item = {key: nested}
            else:
                item = {key: None}
            if pos < len(lines) and lines[pos][1] == indent + INDENT_UNIT \
                    and not lines[pos][2].startswith("- "):
                sub, pos = _parse_map(lines, pos, indent + INDENT_UNIT)
                item.update(sub)
            result.append(item)
        else:
            result.append(_scalar(body))
    return result, pos


def loads(text: str):
    """@brief 解析受限 YAML 子集文本 @return dict/list"""
    lines = _lines(text)
    if not lines:
        return {}
    value, pos = _parse_block(lines, 0, lines[0][1])
    if pos != len(lines):
        raise YamliteError(f"第 {lines[pos][0]} 行:顶层缩进不一致")
    return value


def load_file(path: str):
    """@brief 解析文件"""
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())
