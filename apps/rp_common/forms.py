# -*- coding: utf-8 -*-
"""
@file    forms.py
@brief   RP 共享表单解析:urlencoded(轻表单)与 multipart(文件上传)统一
         入口(离线环境无 python-multipart,复用平台自研解析,ARC-5)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import urllib.parse

from fastapi import Request

from apps.rp_common.multipart import parse_multipart

MAX_FORM_BYTES = 64 * 1024            # 纯文本表单上限
MAX_MULTIPART_BYTES = 40 * 1024 * 1024  # 上传表单上限(证件 20MB+笔记图余量)


async def read_form(request: Request) -> dict:
    """@brief 读取 urlencoded 表单 @return {字段: 首值}"""
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        return {}
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"),
                                   keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


async def read_any_form(request: Request) -> tuple:
    """
    @brief  按 Content-Type 自动选择解析器
    @return (fields: dict, files: dict[name → list[(filename, bytes)]])
    """
    content_type = request.headers.get("content-type", "")
    raw = await request.body()
    if "multipart/form-data" in content_type:
        if len(raw) > MAX_MULTIPART_BYTES:
            return {}, {}
        return parse_multipart(raw, content_type)
    if len(raw) > MAX_FORM_BYTES:
        return {}, {}
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"),
                                   keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}, {}


def form_bool(fields: dict, key: str, default: bool = False) -> bool:
    """@brief 表单布尔(缺省回退 default;1/true/on/yes 计真)"""
    raw = fields.get(key)
    if raw is None or raw == "":
        return default
    return str(raw).lower() in ("1", "true", "on", "yes")


def form_float(fields: dict, key: str, default: float) -> float:
    """@brief 表单浮点(空/非法回退 default)"""
    try:
        return float(fields.get(key, ""))
    except (TypeError, ValueError):
        return default


def form_int(fields: dict, key: str, default: int) -> int:
    """@brief 表单整数(空/非法回退 default)"""
    try:
        return int(fields.get(key, ""))
    except (TypeError, ValueError):
        return default
