# -*- coding: utf-8 -*-
"""
@file    sso_routes.py
@brief   RP 五路由工厂(H08 §3 统一接入契约):/sso/status|login|callback|logout
         + POST /backchannel-logout。回调完成 SSO 账户映射(自动建号)后发
         RP 本地会话 Cookie;停用账户在映射点拦截;会话逐请求可经
         require_session 校验(令牌逐请求回库,H03 §3)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from gd_common.errors import CryptoError, PlatformError
from gd_common.jsonlog import get_logger
from gd_sso_client.client import SsoClient
from apps.rp_common.accounts import RpAccountService, STATUS_ACTIVE

_log = get_logger("rp.sso")

DEFAULT_COOKIE_NAME = "gd_rp_sid"


def build_sso_router(sso: SsoClient, accounts: RpAccountService,
                     cookie_name: str = DEFAULT_COOKIE_NAME,
                     cookie_secure: bool = True) -> APIRouter:
    """
    @brief  构造 RP 五路由(四系统复用同一实现)
    @param  sso       已装配的 SsoClient
    @param  accounts  本系统账户映射服务
    @param  cookie_name  RP 会话 Cookie 名(SSO_COOKIE_NAME 可覆盖)
    @param  cookie_secure Secure 属性(生产必须 HTTPS;禁用关 Secure"修"登录,06-E13)
    """
    router = APIRouter()

    @router.get("/sso/status")
    def sso_status():
        """@brief 登录页据此显隐 SSO 按钮(H08 §3)"""
        return sso.status()

    @router.get("/sso/login")
    def sso_login(next: str = "/"):
        """@brief 生成 state/nonce/PKCE 并 302 到 IdP authorize"""
        try:
            return RedirectResponse(sso.build_login_redirect(next), status_code=302)
        except PlatformError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.get("/sso/callback")
    def sso_callback(request: Request):
        """@brief 换令牌→验签→账户映射(自动建号)→发本地会话→302 next"""
        query = dict(request.query_params)
        try:
            result = sso.handle_callback(query)
        except CryptoError as exc:
            _log.warning("SSO 回调验签失败", extra={"ctx": {"error": str(exc)}})
            return JSONResponse({"error": "SSO 令牌校验失败"}, status_code=401)
        except PlatformError as exc:        # state 过期/重放等
            return JSONResponse({"error": str(exc)}, status_code=403)
        try:
            local_user = accounts.ensure_sso_account(result["claims"])
        except PlatformError as exc:
            # 停用账户拦截(H08 §3):回滚刚建立的本地会话
            sso.revoke_session(result["session_id"])
            return JSONResponse({"error": str(exc)}, status_code=403)
        response = RedirectResponse(result["next"], status_code=302)
        response.set_cookie(cookie_name, result["session_id"], httponly=True,
                            samesite="lax", secure=cookie_secure)
        _log.info("SSO 登录完成", extra={"ctx": {
            "username": local_user["username"], "role": local_user["role"]}})
        return response

    @router.get("/sso/logout")
    def sso_logout(request: Request):
        """@brief 注销本地会话并跳转(post_logout 可配)"""
        session_id = request.cookies.get(cookie_name, "")
        if session_id:
            sso.revoke_session(session_id)
        response = RedirectResponse(sso.post_logout_url(), status_code=302)
        response.delete_cookie(cookie_name)
        return response

    @router.post("/backchannel-logout")
    async def backchannel_logout(request: Request):
        """@brief IdP 扇出:验 logout_token 即刻吊销该用户全部本地会话"""
        body = (await request.body()).decode("utf-8", errors="replace")
        token = _extract_logout_token(body)
        try:
            sub = sso.handle_backchannel_logout(token)
        except (CryptoError, PlatformError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _log.info("backchannel 登出完成", extra={"ctx": {"sub": sub}})
        return {"revoked_sub": sub}

    return router


def _extract_logout_token(body: str) -> str:
    """@brief 从 urlencoded 体取 logout_token(OIDC back-channel 规范格式)"""
    import urllib.parse
    parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
    return parsed.get("logout_token", [""])[0]


def require_session(request: Request, sso: SsoClient,
                    accounts: RpAccountService,
                    cookie_name: str = DEFAULT_COOKIE_NAME):
    """
    @brief  RP 受保护路由的会话校验:取 Cookie→查易失态会话→逐请求回库校验
            账户状态(停用/锁定即刻切断在线会话,H03 §3 token_recheck_per_request)
    @return (local_user, None) 或 (None, 错误响应)
    """
    session = sso.get_session(request.cookies.get(cookie_name, ""))
    if session is None:
        return None, JSONResponse({"error": "未登录"}, status_code=401)
    user = accounts.get_by_sub(session["sub"])
    if user is None or user["status"] != STATUS_ACTIVE:
        return None, JSONResponse({"error": "账户不可用"}, status_code=403)
    return user, None


def require_role(user: dict, allowed: tuple):
    """@brief 角色闸门:不在允许集合返回 403 响应,否则 None(H03 §1 最小权限)"""
    if user["role"] not in allowed:
        return JSONResponse({"error": "权限不足"}, status_code=403)
    return None
