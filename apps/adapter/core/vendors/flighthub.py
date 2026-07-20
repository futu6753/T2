# -*- coding: utf-8 -*-
"""
@file    flighthub.py
@brief   大疆司空 2 / FlightHub 南向客户端骨架(feature=planned):
         X-User-Token 鉴权形态先行落地;Sync 推送启用后按通用信封落
         raw_log 并产成型事件保证链路不断。TODO(GAP-24):鉴权/信封/
         Sync 推送体真实形态待联调,启用后换专用翻译器 + 验签。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from apps.adapter.core.errors import UpstreamError
from apps.adapter.core.vendors.transport import gated_base_url, json_body


class FlightHubClient:
    """司空2 客户端骨架(传输可注入)。"""

    def __init__(self, settings, transport):
        """@brief 绑定配置与传输"""
        self.settings = settings
        self.transport = transport

    @property
    def configured(self) -> bool:
        """@brief 自动门控"""
        return not gated_base_url(self.settings.fh2_base_url)

    def _headers(self) -> dict:
        """@brief X-User-Token 鉴权头"""
        return {"Content-Type": "application/json",
                "X-User-Token": self.settings.fh2_user_token}

    def request(self, path: str, payload: dict) -> dict:
        """@brief 通用请求入口(启用后专用端点在此之上扩展)"""
        resp = self.transport("POST", self.settings.fh2_base_url + path,
                              headers=self._headers(),
                              body=json_body(payload))
        if resp.status >= 500:
            raise UpstreamError(f"司空2 接口 {path} 异常:HTTP {resp.status}")
        return resp.json()
