# -*- coding: utf-8 -*-
"""
@file    web_admin.py
@brief   管理台五区路由(02-A4):用户/组/应用/链接/审计;全部 POST 带 CSRF 令牌、
         留审计;末位 admin 守护与防自锁总则(H03 §4);模式切换页(H05 §3)。
         本里程碑以 JSON API 形态交付,页面渲染随里程碑 9 前端统一。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gd_common.errors import PlatformError
from gd_storage import events, make_key, verify_chain
from apps.idp.forms import form_bool, read_form
from apps.idp.mode import ModeService

CSRF_TTL_SECONDS = 3600
HTTP_FORBIDDEN = 403
HTTP_UNAUTHORIZED = 401
SESSION_COOKIE = "gd_idp_sid"        # 与 web.py 保持一致(单一 Cookie 名)


def build_admin_router(ctx) -> APIRouter:
    """@brief 组装 /admin 路由(上下文闭包注入)"""
    router = APIRouter(prefix="/admin")
    mode_service = ModeService(ctx)

    def _require_admin(request: Request):
        """@brief 管理员会话校验 @return (user, 错误响应|None)"""
        session = ctx.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return None, JSONResponse({"error": "未登录"},
                                      status_code=HTTP_UNAUTHORIZED)
        user = ctx.accounts.get_user(session["account"])
        if user is None or not user["is_admin"]:
            return None, JSONResponse({"error": "需要管理员权限"},
                                      status_code=HTTP_FORBIDDEN)
        return user, None

    def _check_csrf(request: Request, csrf_token: str):
        """@brief CSRF 令牌校验(管理操作全覆盖,H03 §3)"""
        sid = request.cookies.get(SESSION_COOKIE, "")
        stored = ctx.store.get(make_key("idp", "csrf", sid))
        if not stored or stored != csrf_token:
            return JSONResponse({"error": "CSRF 校验失败"},
                                status_code=HTTP_FORBIDDEN)
        return None

    @router.get("/csrf")
    def csrf_token(request: Request):
        """@brief 为当前管理员会话签发 CSRF 令牌"""
        user, error = _require_admin(request)
        if error:
            return error
        sid = request.cookies.get(SESSION_COOKIE)
        token = secrets.token_urlsafe(24)
        ctx.store.set(make_key("idp", "csrf", sid), token,
                      ttl_seconds=CSRF_TTL_SECONDS)
        return {"csrf_token": token}

    # ---- 用户区 -----------------------------------------------------------
    @router.post("/users/create")
    async def user_create(request: Request):
        """@brief 建号(首登强改密)"""
        user, error = _require_admin(request)
        if error:
            return error
        form = await read_form(request)
        error = _check_csrf(request, form.get("csrf_token", ""))
        if error:
            return error
        try:
            ctx.accounts.create_user(
                form.get("account", ""), form.get("display_name", ""),
                form.get("password", ""), ctx.profile, user["account"], "0.0.0.0",
                is_admin=form_bool(form, "is_admin"))
        except PlatformError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"created": form.get("account", "")}

    def _admin_count_excluding(account: str) -> int:
        """@brief 除指定账号外的可用管理员数(末位 admin 守护依据)"""
        rows = ctx.db.query(
            "SELECT COUNT(*) FROM idp_users WHERE is_admin = 1"
            " AND status = 'active' AND account != ?", (account,))
        return rows[0][0]

    @router.post("/users/disable")
    async def user_disable(request: Request):
        """@brief 停用账号(即刻断线;末位 admin 守护,H03 §4)"""
        user, error = _require_admin(request)
        if error:
            return error
        form = await read_form(request)
        account = form.get("account", "")
        error = _check_csrf(request, form.get("csrf_token", ""))
        if error:
            return error
        target = ctx.accounts.get_user(account)
        if target is None:
            return JSONResponse({"error": "账号不存在"}, status_code=404)
        if target["is_admin"] and _admin_count_excluding(account) == 0:
            return JSONResponse(
                {"error": "禁止停用最后一名管理员(末位 admin 守护)"},
                status_code=400)
        ctx.db.execute("UPDATE idp_users SET status = 'disabled' WHERE account = ?",
                       (account,))
        ctx.sessions.revoke_user(account)     # 停用即刻断线(H04 §二.b)
        ctx.audit.append(user["account"], events.USER_DISABLED,
                         {"account": account}, "0.0.0.0")
        return {"disabled": account}

    @router.post("/users/unlock")
    async def user_unlock(request: Request):
        """@brief 管理员立即解锁(只解锁不改密,06-E5)"""
        user, error = _require_admin(request)
        if error:
            return error
        form = await read_form(request)
        error = _check_csrf(request, form.get("csrf_token", ""))
        if error:
            return error
        account = form.get("account", "")
        ctx.accounts.admin_unlock(account, user["account"], "0.0.0.0")
        return {"unlocked": account}

    # ---- 应用区 / 链接区 --------------------------------------------------
    @router.post("/clients/create")
    async def client_create(request: Request):
        """@brief 注册应用(返回一次性明文密钥)"""
        user, error = _require_admin(request)
        if error:
            return error
        form = await read_form(request)
        error = _check_csrf(request, form.get("csrf_token", ""))
        if error:
            return error
        client_id = form.get("client_id", "")
        secret = ctx.oidc.create_client(
            client_id, form.get("name", ""), [form.get("redirect_uri", "")],
            backchannel_url=form.get("backchannel_url") or None)
        ctx.audit.append(user["account"], events.SETTINGS_CHANGED,
                         {"key": "client_created", "client_id": client_id}, "0.0.0.0")
        return {"client_id": client_id, "client_secret_once": secret}

    # ---- 审计区(近 N 条 + 一键校验,02-A4) ------------------------------
    @router.get("/audit")
    def audit_view(request: Request, limit: int = 50):
        """@brief 审计日志查看(近 N 条,分页参数钳制)"""
        _, error = _require_admin(request)
        if error:
            return error
        limit = max(1, min(int(limit), 500))
        rows = ctx.db.query(
            "SELECT id, ts, actor, action, ip FROM audit_logs"
            " ORDER BY id DESC LIMIT ?", (limit,))
        return {"entries": [dict(zip(("id", "ts", "actor", "action", "ip"), row))
                            for row in rows]}

    @router.get("/audit/verify")
    def audit_verify(request: Request):
        """@brief 审计哈希链一键校验"""
        _, error = _require_admin(request)
        if error:
            return error
        try:
            count = verify_chain(ctx.db)
        except PlatformError as exc:
            return JSONResponse({"chain": "BROKEN", "error": str(exc)},
                                status_code=500)
        return {"chain": "OK", "records": count}

    # ---- 模式区(H05 §3) --------------------------------------------------
    @router.get("/mode")
    def mode_view(request: Request):
        """@brief 当前运行模式与来源层"""
        _, error = _require_admin(request)
        if error:
            return error
        value, source = ctx.settings.get_with_source("demo_mode")
        return {"mode": ctx.profile.mode, "demo_mode_source": source,
                "env_locked": source == "env"}

    @router.post("/mode/switch")
    async def mode_switch(request: Request):
        """@brief 模式热切换(生产→DEMO 需二次确认+原因)"""
        user, error = _require_admin(request)
        if error:
            return error
        form = await read_form(request)
        error = _check_csrf(request, form.get("csrf_token", ""))
        if error:
            return error
        target = form.get("target", "")
        try:
            if target == "prod":
                return mode_service.switch_to_prod(user["account"], "0.0.0.0")
            if target == "demo":
                return mode_service.switch_to_demo(
                    user["account"], "0.0.0.0", form_bool(form, "confirm"),
                    form.get("reason", ""))
        except PlatformError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"error": "target 必须为 demo|prod"}, status_code=400)

    return router
