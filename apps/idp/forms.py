# -*- coding: utf-8 -*-
"""
@file    forms.py
@brief   表单解析工具:离线环境无 python-multipart,自研 urlencoded 解析
         (ARC-5 最小第三方依赖;管理台服务端表单均为 urlencoded)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import urllib.parse

from fastapi import Request

MAX_FORM_BYTES = 64 * 1024      # 表单体积上限(H04 §四 数据有效性校验)


async def read_form(request: Request) -> dict:
    """
    @brief  读取 application/x-www-form-urlencoded 表单(超限截断拒绝)
    @param  request FastAPI 请求
    @return {字段: 首值} 字典
    """
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        return {}
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"),
                                   keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def form_bool(form: dict, key: str) -> bool:
    """@brief 表单布尔字段(1/true/on 计真)"""
    return str(form.get(key, "")).lower() in ("1", "true", "on", "yes")
