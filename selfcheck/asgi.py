# -*- coding: utf-8 -*-
"""
@file    asgi.py
@brief   进程内 ASGI 客户端:离线环境无 httpx,自研最小实现完成 HTTP 级验证
         (H06-E17:环境受限必须转为可一键执行入口,不得搪塞)。
         支持 GET/POST、表单体、Cookie 保持、重定向读取。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import asyncio
import json as json_mod
import urllib.parse

DEFAULT_HOST = b"testserver"


class AsgiResponse:
    """最小响应对象。"""

    def __init__(self, status: int, headers: list, body: bytes):
        """@brief 组装响应"""
        self.status_code = status
        self.headers = {key.decode().lower(): value.decode()
                        for key, value in headers}
        self.body = body

    def json(self) -> dict:
        """@brief 按 JSON 解析响应体"""
        return json_mod.loads(self.body)

    @property
    def text(self) -> str:
        """@brief 响应体文本"""
        return self.body.decode("utf-8", errors="replace")


class AsgiClient:
    """进程内 ASGI 调用客户端(同步外观,内部跑事件循环)。"""

    def __init__(self, app):
        """@brief 绑定 ASGI 应用,初始化 Cookie 罐"""
        self._app = app
        self.cookies = {}

    def _cookie_header(self) -> bytes:
        """@brief 组装 Cookie 请求头"""
        return "; ".join(f"{name}={value}"
                         for name, value in self.cookies.items()).encode()

    def _absorb_cookies(self, headers: list):
        """@brief 吸收 Set-Cookie(支持删除语义)"""
        for key, value in headers:
            if key.lower() == b"set-cookie":
                fragment = value.decode().split(";", 1)[0]
                name, _, cookie_value = fragment.partition("=")
                if cookie_value == '""' or cookie_value == "":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = cookie_value

    async def _call(self, method: str, path: str, headers: list, body: bytes):
        """@brief 执行一次 ASGI 请求"""
        parsed = urllib.parse.urlsplit(path)
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": parsed.path,
            "raw_path": parsed.path.encode(), "root_path": "",
            "query_string": parsed.query.encode(),
            "headers": [(b"host", DEFAULT_HOST)] + headers,
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        }
        received = {"sent": False}

        async def receive():
            """@brief 一次性请求体"""
            if received["sent"]:
                return {"type": "http.disconnect"}
            received["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        result = {"status": 500, "headers": [], "body": b""}

        async def send(message):
            """@brief 收集响应事件"""
            if message["type"] == "http.response.start":
                result["status"] = message["status"]
                result["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                result["body"] += message.get("body", b"")

        await self._app(scope, receive, send)
        return result

    def request(self, method: str, path: str, data: dict = None,
                headers: dict = None, raw_body: bytes = None,
                content_type: str = None) -> AsgiResponse:
        """@brief 发起请求(data 表单编码;raw_body+content_type 用于 multipart)"""
        header_list = [(key.lower().encode(), value.encode())
                       for key, value in (headers or {}).items()]
        if self.cookies:
            header_list.append((b"cookie", self._cookie_header()))
        body = b""
        if raw_body is not None:
            body = raw_body
            header_list.append((b"content-type",
                                (content_type or "application/octet-stream")
                                .encode()))
            header_list.append((b"content-length", str(len(body)).encode()))
        elif data is not None:
            body = urllib.parse.urlencode(data).encode("ascii")
            header_list.append(
                (b"content-type", b"application/x-www-form-urlencoded"))
            header_list.append((b"content-length", str(len(body)).encode()))
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._call(method, path, header_list, body))
        finally:
            loop.close()      # 资源即用即还(H07 L1-11)
        self._absorb_cookies(result["headers"])
        return AsgiResponse(result["status"], result["headers"], result["body"])

    def get(self, path: str, headers: dict = None) -> AsgiResponse:
        """@brief GET 请求"""
        return self.request("GET", path, headers=headers)

    def post(self, path: str, data: dict = None,
             headers: dict = None) -> AsgiResponse:
        """@brief POST 表单请求"""
        return self.request("POST", path, data=data, headers=headers)
