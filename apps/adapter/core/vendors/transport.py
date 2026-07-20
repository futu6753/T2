# -*- coding: utf-8 -*-
"""
@file    transport.py
@brief   南向 HTTP 传输层(纯标准库 urllib):可注入替身(测试以假传输
         mock 上游,H06-E17"错误映射每个分支要有 mock 级活体测试")。
         发出前逐头做 latin-1 安全检查(M17 防回潮)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import socket
import urllib.error
import urllib.request

from apps.adapter.core.config import ensure_header_safe
from apps.adapter.core.errors import UpstreamError


class TransportTimeout(Exception):
    """传输层超时(命令链路上由 dispatch 翻译为 504)。"""


class HttpResponse:
    """传输层响应(最小结构)。"""

    def __init__(self, status: int, headers: dict, body: bytes):
        """@brief 组装响应"""
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        """@brief 按 JSON 解析响应体(失败 → UpstreamError,502)"""
        try:
            return json.loads(self.body or b"{}")
        except ValueError as exc:
            raise UpstreamError(f"上游响应非 JSON:{exc}") from exc


class HttpTransport:
    """标准库实现的传输(生产默认;测试注入可调用替身)。"""

    def __init__(self, timeout_s: float = 10.0):
        """@brief 默认超时"""
        self.timeout_s = timeout_s

    def __call__(self, method: str, url: str, headers: dict = None,
                 body: bytes = None, timeout_s: float = None) -> HttpResponse:
        """@brief 执行一次 HTTP 请求(网络错误→UpstreamError,超时→TransportTimeout)"""
        safe_headers = {}
        for name, value in (headers or {}).items():
            safe_headers[name] = ensure_header_safe(name, str(value))
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=safe_headers)
        try:
            with urllib.request.urlopen(
                    request, timeout=timeout_s or self.timeout_s) as resp:
                return HttpResponse(resp.status, dict(resp.headers),
                                    resp.read())
        except socket.timeout as exc:
            raise TransportTimeout(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, dict(exc.headers or {}),
                                exc.read())
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TransportTimeout(str(exc)) from exc
            raise UpstreamError(f"上游连接失败:{exc.reason}") from exc


def gated_base_url(base_url: str) -> bool:
    """@brief 自动门控(L01 §7):BASE_URL 空或含 example.com 不发请求"""
    return (not base_url) or ("example.com" in base_url)


def json_body(payload: dict) -> bytes:
    """@brief JSON 请求体编码"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
