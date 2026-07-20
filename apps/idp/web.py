# -*- coding: utf-8 -*-
"""
@file    web.py
@brief   IdP Web 核心路由(02-A1/A2/A5):OIDC 五端点、口令/TOTP/短信登录、
         D4 证书测试入口与 D5 微信模拟(生产 404)、/portal、/healthz、
         X-Request-Id 中间件、DEMO 横幅注入。管理台见 web_admin.py。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import html
import json
import secrets
import urllib.parse
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from gd_common.jsonlog import get_logger, request_id_var
from gd_crypto import issue_context, verify_context
from gd_common.errors import (
    ExpiredContextError, InvalidContextError, PolicyValidationError,
)
from gd_crypto.context_token import renew_context
from apps.idp import accounts as acc
from apps.idp.context import IdpContext
from apps.idp.sessions import AMR_DEMO_MARK
from apps.idp.forms import read_form
from apps.idp.web_admin import build_admin_router

_log = get_logger("idp.web")
SESSION_COOKIE = "gd_idp_sid"
LOGIN_CONTEXT_TTL = 1800          # 登录上下文 30 分钟(02-A3)
HTTP_NOT_FOUND = 404
HTTP_LOCKED = 423
HTTP_UNAUTHORIZED = 401


# 浏览器人话报错文案(06-E18:PRG 后经 ?err= 渲染,禁止对浏览器回裸 JSON)
_LOGIN_ERR_TEXT = {
    "cred": "用户名或口令错误(启用动态码的账户须同时填写动态码)",
    "locked": "账号已锁定,请稍后再试或联系管理员解锁",
    "ctx": "登录上下文无效或已过期,请回到业务系统重新发起登录",
}


def _login_page_html(ctx: IdpContext, rid: str = "", err: str = "",
                     changed: bool = False) -> str:
    """
    @brief  登录页:可操作极简表单(06-E18)。含账号/口令/动态码输入与提交按钮、
            隐藏 rid 透传(06-E19:SSO 授权链不断),err/changed 人话提示。
    @param  rid     authorize 302 带来的登录上下文(html.escape 后回显)
    @param  err     PRG 错误码(cred|locked|ctx)
    @param  changed True=改密成功提示
    """
    banner = ctx.profile.banner_text
    color = "#c62828" if ctx.profile.is_demo else "#2e7d32"
    tabs = "口令|短信|TOTP|微信|证书"
    notice = ("<p id='login-notice' style='color:#2e7d32'>"
              "口令修改成功,请使用新口令登录</p>" if changed else "")
    err_text = _LOGIN_ERR_TEXT.get(err, "")
    err_html = (f"<p id='login-error' style='color:#c62828'>{err_text}</p>"
                if err_text else "")
    rid_safe = html.escape(rid, quote=True)
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>港电统一登录</title></head><body>"
            f"<div style='background:{color};color:#fff'>{banner}</div>"
            f"<div id='login-tabs' hidden>{tabs}</div>"
            f"<style>[hidden]{{display:none}}</style>"       # 06-E3 兜底条款
            f"<h3>统一身份认证</h3>{notice}{err_html}"
            f"<form id='login-form' method='post' action='/login'>"
            f"<input type='hidden' name='rid' value='{rid_safe}'>"
            f"<label>账号 <input name='account' autocomplete='username'></label>"
            f"<label>口令 <input name='password' type='password'"
            f" autocomplete='current-password'></label>"
            f"<label>动态码 <input name='totp_code'"
            f" placeholder='未开启可留空'></label>"
            f"<button type='submit' id='login-submit'>登 录</button>"
            f"</form>"
            f"<script>document.getElementById('login-form');</script>"
            f"</body></html>")


def _login_redirect(rid: str, err: str):
    """@brief 浏览器登录失败 PRG:303 回登录页,rid 原样透传(06-E19)"""
    query = urllib.parse.urlencode(
        {key: value for key, value in (("err", err), ("rid", rid)) if value})
    return RedirectResponse(f"/login?{query}", status_code=303)


def _set_session_cookie(response: Response, ctx: IdpContext, sid: str):
    """@brief 下发会话 Cookie(生产强制 Secure,禁以关 Secure 修登录,06-E13)"""
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax",
                        secure=ctx.profile.cookie_secure_required)


def _issue_login_context(ctx: IdpContext, payload: dict) -> str:
    """@brief 签发无状态登录上下文 rid(02-A3,重启/多实例可验签还原)"""
    return issue_context(payload, ctx.ring.current_key, ctx.suite,
                         LOGIN_CONTEXT_TTL)


def _restore_login_context(ctx: IdpContext, rid: str) -> tuple:
    """@brief 还原登录上下文;过期自动续签而非死路(06-E2) @return (载荷, 新rid|None)"""
    try:
        return verify_context(rid, ctx.ring.current_key, ctx.suite), None
    except ExpiredContextError as exc:
        renewed = renew_context(exc.payload, ctx.ring.current_key, ctx.suite,
                                LOGIN_CONTEXT_TTL)
        return exc.payload, renewed


def create_app(ctx: IdpContext) -> FastAPI:
    """@brief 组装 IdP FastAPI 应用(上下文注入,便于测试构造)"""
    app = FastAPI(title="港电统一认证中心", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.include_router(build_admin_router(ctx))

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """@brief X-Request-Id 全链路贯通(ARC-6)+ 设置版本轮询(G.4)"""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(request_id)
        ctx.maybe_refresh()      # 他实例改策略后本实例下一请求即热生效
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    # ---- OIDC 五端点(02-A1) -------------------------------------------
    @app.get("/.well-known/openid-configuration")
    def discovery():
        """@brief OIDC 发现文档"""
        return ctx.oidc.discovery_document()

    @app.get("/jwks.json")
    def jwks():
        """@brief JWKS 公钥发布"""
        return ctx.keys.jwks()

    @app.get("/authorize")
    def authorize(request: Request, client_id: str = "", redirect_uri: str = "",
                  state: str = "", nonce: str = "", code_challenge: str = "",
                  code_challenge_method: str = "", response_type: str = "code"):
        """@brief 授权端点:未登录带 rid 转登录页;已登录校验访问策略并发码"""
        client, error = ctx.oidc.validate_authorize(
            client_id, redirect_uri, code_challenge, code_challenge_method)
        if error:
            return JSONResponse({"error": error}, status_code=400)
        session = ctx.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            rid = _issue_login_context(ctx, {
                "client_id": client_id, "redirect_uri": redirect_uri,
                "state": state, "nonce": nonce, "code_challenge": code_challenge})
            return RedirectResponse(f"/login?rid={rid}", status_code=302)
        user = ctx.accounts.get_user(session["account"])
        groups = ctx.accounts.user_groups(user["id"])
        if not ctx.oidc.check_access(client, bool(user["is_admin"]), groups):
            return RedirectResponse(
                f"{redirect_uri}?error=access_denied&state={state}", status_code=302)
        code = ctx.oidc.issue_auth_code(client_id, redirect_uri, user["account"],
                                        session["amr"], nonce, code_challenge)
        return RedirectResponse(f"{redirect_uri}?code={code}&state={state}",
                                status_code=302)

    @app.post("/token")
    async def token(request: Request):
        """@brief 令牌端点(授权码换 id_token/access_token)"""
        form = await read_form(request)
        if form.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        result, error = ctx.oidc.exchange_code(
            form.get("client_id", ""), form.get("client_secret", ""),
            form.get("code", ""), form.get("redirect_uri", ""),
            form.get("code_verifier", ""), ctx.user_lookup)
        if error:
            return JSONResponse({"error": error}, status_code=400)
        return result

    @app.get("/userinfo")
    def userinfo(request: Request):
        """@brief 用户信息端点(Bearer access_token)"""
        auth_header = request.headers.get("authorization", "")
        info = ctx.oidc.userinfo(auth_header.removeprefix("Bearer ").strip())
        if info is None:
            return JSONResponse({"error": "invalid_token"},
                                status_code=HTTP_UNAUTHORIZED)
        return info

    @app.get("/logout")
    def logout(request: Request):
        """@brief 登出:吊销本端会话并向各 RP back-channel 扇出"""
        sid = request.cookies.get(SESSION_COOKIE)
        session = ctx.sessions.get(sid)
        if session:
            ctx.sessions.revoke(sid)
            deliveries = []
            ctx.oidc.backchannel_fanout(
                session["account"],
                lambda url, token: deliveries.append((url, token)))
            app.state.pending_backchannel = deliveries   # 投递由部署侧 worker 消费
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(SESSION_COOKIE)
        return response

    # ---- 登录(口令/TOTP/短信;02-A2) -----------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(rid: str = "", err: str = "", changed: str = ""):
        """@brief 登录页(含 DEMO/生产横幅,05-D9;rid 透传+PRG 提示,06-E18/E19)"""
        return _login_page_html(ctx, rid=rid, err=err, changed=changed == "1")

    def _complete_login(user: dict, rid_payload: dict, method: str,
                        amr_extra: list = None):
        """@brief 建会话+回跳 OIDC 流程(若来自 authorize)"""
        amr = [method] + (amr_extra or [])
        if ctx.profile.is_demo and user["status"] == acc.STATUS_DEMO:
            amr.append(AMR_DEMO_MARK)
        ctx.accounts.finish_login(user, "0.0.0.0", method)
        sid = ctx.sessions.create(user["account"], amr)
        if rid_payload and rid_payload.get("client_id"):
            target = ("/authorize?client_id={client_id}&redirect_uri={redirect_uri}"
                      "&state={state}&nonce={nonce}&code_challenge={code_challenge}"
                      "&code_challenge_method=S256").format(**rid_payload)
        else:
            target = "/portal"
        response = RedirectResponse(target, status_code=302)
        _set_session_cookie(response, ctx, sid)
        return response

    @app.post("/login")
    async def login_password(request: Request):
        """@brief 口令登录(启用 TOTP 的账户=口令+动态码两步)"""
        form = await read_form(request)
        account, password = form.get("account", ""), form.get("password", "")
        totp_code, rid = form.get("totp_code", ""), form.get("rid", "")
        wants_html = "text/html" in request.headers.get("accept", "")
        rid_payload = None
        if rid:
            try:
                rid_payload, _ = _restore_login_context(ctx, rid)
            except InvalidContextError:
                if wants_html:                      # 06-E18:浏览器走 PRG 人话报错
                    return _login_redirect("", "ctx")
                return JSONResponse({"error": "登录上下文无效"}, status_code=400)
        result, user = ctx.accounts.password_login_step(
            account, password, ctx.profile, "0.0.0.0")
        if result == acc.LOGIN_LOCKED:
            if wants_html:
                return _login_redirect(rid, "locked")
            return JSONResponse(
                {"error": f"账号已锁定,请 {ctx.profile.lockout_minutes} 分钟后再试"},
                status_code=HTTP_LOCKED)
        if result == acc.LOGIN_FAILED:
            if wants_html:
                return _login_redirect(rid, "cred")
            return JSONResponse({"error": "用户名或口令错误"},
                                status_code=HTTP_UNAUTHORIZED)
        if result == acc.LOGIN_NEED_TOTP:
            if not ctx.accounts.verify_totp_step(user, totp_code, ctx.profile,
                                                 "0.0.0.0"):
                if wants_html:
                    return _login_redirect(rid, "cred")
                return JSONResponse({"error": "用户名或口令错误"},
                                    status_code=HTTP_UNAUTHORIZED)
        if result == acc.LOGIN_MUST_CHANGE:
            # 口令已验证正确 → 签发改密票据(无状态上下文,5 分钟)
            ticket = _issue_login_context(
                ctx, {"purpose": "pwd_change", "account": user["account"]})
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(
                    f"/account/password?rid={ticket}", status_code=303)
            return JSONResponse({"error": "首次登录必须修改口令",
                                 "next": "/account/password",
                                 "rid": ticket}, status_code=403)
        return _complete_login(user, rid_payload, "pwd")

    @app.get("/account/password", response_class=HTMLResponse)
    def password_page(rid: str = ""):
        """@brief 改密页(首登强改/口令到期共用;极简页,完整 UI 随里程碑 9)"""
        account_hint = ""
        if rid:
            try:
                payload, _ = _restore_login_context(ctx, rid)
                if payload.get("purpose") == "pwd_change":
                    account_hint = payload.get("account", "")
            except InvalidContextError:
                rid = ""
        return (f"<!doctype html><html><body>"
                f"<h3>修改口令</h3>"
                f"<p>口令须 ≥{ctx.profile.password_min_length} 位且含"
                f"大小写/数字/符号至少三类</p>"
                f"<form method='post' action='/account/password'>"
                f"<input type='hidden' name='rid' value='{rid}'>"
                f"<input name='account' placeholder='账号'"
                f" value='{account_hint}'{' readonly' if account_hint else ''}>"
                f"<input name='old_password' type='password' placeholder='原口令'>"
                f"<input name='new_password' type='password' placeholder='新口令'>"
                f"<input name='confirm_password' type='password'"
                f" placeholder='确认新口令'>"
                f"<input name='totp_code' placeholder='动态码(未开启可留空)'>"
                f"<button type='submit'>修改并返回登录</button>"
                f"</form></body></html>")

    @app.post("/account/password")
    async def password_change(request: Request):
        """
        @brief  自助改密(旧口令即认证,失败复用锁定计数;启用 TOTP 的账户
                须附动态码)。浏览器 303 回登录页,API 返回 JSON。
        """
        form = await read_form(request)
        account = form.get("account", "")
        old_password = form.get("old_password", "")
        new_password = form.get("new_password", "")
        wants_html = "text/html" in request.headers.get("accept", "")
        if new_password != form.get("confirm_password", ""):
            return JSONResponse({"error": "两次输入的新口令不一致"},
                                status_code=400)
        if new_password == old_password:
            return JSONResponse({"error": "新口令不得与原口令相同"},
                                status_code=400)
        result, user = ctx.accounts.password_login_step(
            account, old_password, ctx.profile, "0.0.0.0")
        if result == acc.LOGIN_LOCKED:
            return JSONResponse(
                {"error": f"账号已锁定,请 {ctx.profile.lockout_minutes} 分钟后再试"},
                status_code=HTTP_LOCKED)
        if result == acc.LOGIN_FAILED:
            return JSONResponse({"error": "账号或原口令错误"},
                                status_code=HTTP_UNAUTHORIZED)
        if result == acc.LOGIN_NEED_TOTP:
            if not ctx.accounts.verify_totp_step(
                    user, form.get("totp_code", ""), ctx.profile, "0.0.0.0"):
                return JSONResponse({"error": "动态码缺失或错误"},
                                    status_code=HTTP_UNAUTHORIZED)
        try:
            ctx.accounts.change_password(account, new_password, ctx.profile,
                                         account, "0.0.0.0")
        except PolicyValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if wants_html:
            return RedirectResponse("/login?changed=1", status_code=303)
        return {"changed": True, "next": "/login"}

    @app.post("/login/sms/send")
    async def sms_send(request: Request):
        """@brief 发送短信验证码(D3:DEMO 回显,生产不出现在响应/日志)"""
        account = (await read_form(request)).get("account", "")
        if not ctx.profile.method_sms:
            return JSONResponse({"error": "短信登录未启用"}, status_code=HTTP_NOT_FOUND)
        echo = ctx.accounts.send_sms_code(account, ctx.profile)
        body = {"sent": True}
        if echo is not None:
            body["demo_echo_code"] = echo
        return body

    @app.post("/login/sms/verify")
    async def sms_verify(request: Request):
        """@brief 短信验证码登录"""
        form = await read_form(request)
        account, code, rid = (form.get("account", ""), form.get("code", ""),
                              form.get("rid", ""))
        if not ctx.profile.method_sms:
            return JSONResponse({"error": "短信登录未启用"}, status_code=HTTP_NOT_FOUND)
        if ctx.accounts.is_locked(account):
            return JSONResponse({"error": "账号已锁定"}, status_code=HTTP_LOCKED)
        user = ctx.accounts.get_user(account)
        if user is None or not ctx.accounts.verify_sms_code(account, code,
                                                            ctx.profile, "0.0.0.0"):
            return JSONResponse({"error": "验证码错误"}, status_code=HTTP_UNAUTHORIZED)
        rid_payload = None
        if rid:
            rid_payload, _ = _restore_login_context(ctx, rid)
        return _complete_login(user, rid_payload, "sms")

    # ---- D4/D5 模拟入口(仅 DEMO;生产 404,05-D4/D5) --------------------
    @app.get("/login/cert-demo")
    def cert_demo():
        """@brief 证书测试入口(粘贴 PEM 模拟 mTLS;生产自动关闭)"""
        if not ctx.profile.cert_demo_endpoint_enabled:
            return JSONResponse({"error": "not found"}, status_code=HTTP_NOT_FOUND)
        return HTMLResponse("<form id='cert-demo'>粘贴 PEM 测试 mTLS 流程</form>")

    @app.get("/wx/scan")
    def wechat_scan():
        """@brief 微信模拟扫码入口(DEMO 内置闭环;生产未配置整体隐藏)"""
        if not ctx.profile.wechat_mock_enabled:
            return JSONResponse({"error": "not found"}, status_code=HTTP_NOT_FOUND)
        return {"scene": secrets.token_hex(8), "mock": True}

    # ---- 门户与运维 -------------------------------------------------------
    @app.get("/portal")
    def portal(request: Request):
        """@brief 企业门户:按权限展示应用卡片(02-A5)"""
        session = ctx.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return RedirectResponse("/login", status_code=302)
        user = ctx.accounts.get_user(session["account"])
        groups = ctx.accounts.user_groups(user["id"])
        rows = ctx.db.query("SELECT client_id, name, access_policy, access_groups,"
                            " enabled FROM idp_clients")
        cards = []
        for client_id, name, policy, access_groups, enabled in rows:
            client = {"access_policy": policy,
                      "access_groups": json.loads(access_groups)}
            if enabled and ctx.oidc.check_access(client, bool(user["is_admin"]),
                                                 groups):
                cards.append({"client_id": client_id, "name": name})
        if "text/html" in request.headers.get("accept", ""):
            # 06-E18:浏览器渲染 HTML 卡片页(账号/应用名 html.escape 防注入)
            items = "".join(
                f"<li class='app-card'>{html.escape(card['name'])}</li>"
                for card in cards)
            return HTMLResponse(
                f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>企业门户</title></head><body>"
                f"<div>{html.escape(ctx.profile.banner_text)}</div>"
                f"<h3 id='portal-title'>企业门户 · "
                f"{html.escape(user['account'])}</h3>"
                f"<ul id='portal-apps'>{items}</ul>"
                f"<a href='/logout'>退出登录</a></body></html>")
        return {"banner": ctx.profile.banner_text, "apps": cards,
                "account": user["account"]}

    @app.get("/healthz")
    def healthz():
        """@brief 健康检查:一律返回运行模式与当前密码套件(ARC-6 / 05 §2)"""
        return {"status": "ok", "mode": ctx.profile.mode,
                "crypto_suite": ctx.profile.crypto_suite_name,
                "kid": ctx.keys.kid}

    return app
