# -*- coding: utf-8 -*-
"""
@file    __init__.py
@brief   gd_sso_client:RP 侧统一 OIDC 接入库接口定义(H08 §3 契约)。
         全平台一份库,消灭遗留 4 份拷贝。本里程碑仅落接口(H01 ai_directives:
         "可先空实现"),实现见 client.py(里程碑 2 已交付)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import dataclasses
from typing import Optional, Protocol

# 必填 env(缺任一则 SSO 不启用、系统按原样运行,H08 §3)
REQUIRED_ENV_KEYS = ("SSO_ISSUER", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET", "SSO_REDIRECT")
# RP 侧五路由(契约固定,H08 §3)
RP_ROUTES = ("/sso/status", "/sso/login", "/sso/callback", "/sso/logout",
             "/backchannel-logout")
DEFAULT_SSO_SCOPES = "openid profile"
DEFAULT_SESSION_TTL_SECONDS = 28800


@dataclasses.dataclass(frozen=True)
class SsoConfig:
    """RP 侧 SSO 配置(从环境变量装载;缺必填项则 is_enabled=False)。"""

    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: str = DEFAULT_SSO_SCOPES
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    cookie_name: str = ""                 # 选填 SSO_COOKIE_NAME(空=系统默认)
    cookie_secure: bool = True            # SSO_COOKIE_SECURE(默认 1,06-E13)
    post_logout: str = "/"                # 选填 SSO_POST_LOGOUT 注销后跳转
    default_role: str = ""                # 选填 SSO_DEFAULT_ROLE(空=系统最小角色)
    is_enabled: bool = False


class ISsoClient(Protocol):
    """
    RP 统一接入契约(安全性质 MUST 保持,H08 §3):
    state 一次性 + 10 分钟过期;nonce 绑定防重放;PKCE S256;
    回跳 next 仅站内相对路径;账户锁定/停用对 SSO 同样生效。
    """

    def build_login_redirect(self, next_path: str) -> str:
        """@brief 生成授权跳转 URL(state/nonce/PKCE)@return 302 目标"""
        ...

    def handle_callback(self, query: dict) -> dict:
        """@brief 回调处理:换令牌→验签 iss/aud/exp→返回身份声明 @return claims"""
        ...

    def handle_backchannel_logout(self, logout_token: str) -> str:
        """@brief 验 logout_token 并吊销该用户全部本地会话 @return 受影响用户 sub"""
        ...

    def status(self) -> dict:
        """@brief /sso/status:登录页据此显隐 SSO 按钮 @return {enabled: bool}"""
        ...


# 具体实现见 gd_sso_client.client.SsoClient(里程碑 2 交付,GAP-05 已解除);
# 回归见 tests/test_c_sso_client.py(继承遗留 nvr test_sso 用例语义,06-E13)。
