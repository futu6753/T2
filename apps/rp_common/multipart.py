# -*- coding: utf-8 -*-
"""
@file    multipart.py
@brief   轻量 multipart/form-data 解析器(离线环境无 python-multipart,
         自研以保持 L02 上传契约不变)。仅覆盖本平台上传场景:
         文本字段 + 文件字段,单请求体一次性读入(上限由调用方控制)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import re

from gd_common.errors import PolicyValidationError

_DISPOSITION_RE = re.compile(
    rb'form-data;\s*name="(?P<name>[^"]*)"'
    rb'(?:;\s*filename="(?P<filename>[^"]*)")?', re.IGNORECASE)


def parse_multipart(body: bytes, content_type: str) -> tuple:
    """
    @brief  解析 multipart 请求体
    @param  content_type 含 boundary 的 Content-Type 头
    @return (fields: dict[str,str], files: dict[str, list[(filename, bytes)]])
    @raise  PolicyValidationError 格式非法
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        raise PolicyValidationError("multipart 请求缺少 boundary")
    boundary = b"--" + match.group(1).encode("ascii")
    fields, files = {}, {}
    parts = body.split(boundary)
    for part in parts[1:-1]:                      # 首段为空、末段为 "--\r\n"
        segment = part.strip(b"\r\n")
        if not segment or segment == b"--":
            continue
        if b"\r\n\r\n" not in segment:
            raise PolicyValidationError("multipart 分段缺少头体分隔")
        raw_headers, content = segment.split(b"\r\n\r\n", 1)
        disposition = _DISPOSITION_RE.search(raw_headers)
        if not disposition:
            continue
        name = disposition.group("name").decode("utf-8", errors="replace")
        filename = disposition.group("filename")
        if filename is not None:
            files.setdefault(name, []).append(
                (filename.decode("utf-8", errors="replace"), content))
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, files


def first_file(files: dict, name: str) -> bytes:
    """@brief 取指定字段首个文件内容(缺失返回 None)"""
    entries = files.get(name) or []
    return entries[0][1] if entries else None
