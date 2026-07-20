# -*- coding: utf-8 -*-
"""
@file    web_admin.py
@brief   certvault 管理区(L02 §3):/audit、/admin/locks|unlock、用户管理
         (建号/重置口令=SSO 用户踢下线/停用即刻断线/启用/重置 2FA)、
         审计链一键校验与 CSV 导出。全区 require_admin。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import csv
import io
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from gd_common.errors import PolicyValidationError
from gd_storage import events
from gd_storage.audit import verify_chain
from apps.rp_common.forms import read_form


def build_admin_router(ctx) -> "APIRouter":
    """@brief 管理区路由(CvContext 注入)"""
    router = APIRouter()

    @router.get("/audit")
    def audit_list(request: Request, limit: int = 100):
        """@brief 审计流水(管理员;倒序)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT id, actor, action, detail, ip, ts FROM audit_logs"
            " ORDER BY id DESC LIMIT ?", (min(limit, 500),))
        return {"audit": [
            {"id": row[0], "actor": row[1], "action": row[2],
             "detail": row[3], "ip": row[4], "created_at": row[5]}
            for row in rows]}

    @router.get("/admin/locks")
    def locks(request: Request):
        """@brief 当前锁定账户列表"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        return {"locked": ctx.auth.locked_accounts()}

    @router.post("/admin/unlock")
    async def unlock(request: Request):
        """@brief 管理员立即解锁(留审计)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        form = await read_form(request)
        target = form.get("username", "")
        ctx.auth.clear_failures(target)
        ctx.audit.append(admin["username"], events.USER_UNLOCKED,
                         {"system": "certvault", "target": target}, "0.0.0.0")
        return {"unlocked": target}

    @router.get("/admin/users")
    def list_users(request: Request):
        """@brief 用户列表(角色/状态/2FA/SSO 标记)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT id, username, display_name, role, status, totp_enabled,"
            " sso_sub FROM cv_users ORDER BY id")
        return {"users": [
            {"id": row[0], "username": row[1], "display_name": row[2],
             "role": row[3], "status": row[4], "totp_enabled": bool(row[5]),
             "is_sso": bool(row[6])} for row in rows]}

    @router.post("/admin/users")
    async def create_user(request: Request):
        """@brief 管理员建号(随机口令返回一次 + 首登强改,H03 §2)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        form = await read_form(request)
        one_time_password = "Gd!" + secrets.token_urlsafe(9)
        try:
            user = ctx.auth.register(form.get("username", ""),
                                     one_time_password,
                                     form.get("display_name", ""), "0.0.0.0",
                                     force_role=form.get("role", "user"),
                                     must_change=True)
        except PolicyValidationError as exc:
            return JSONResponse({"error": str(exc)},
                                status_code=getattr(exc, "http_status", 400))
        return {"username": user["username"], "role": user["role"],
                "one_time_password": one_time_password,
                "note": "初始口令仅此一次显示,首次登录须修改"}

    def _target_username(user_id: int):
        """@brief uid → username(路径参数按 L02 用 uid)"""
        rows = ctx.db.query("SELECT username FROM cv_users WHERE id = ?",
                            (user_id,))
        return rows[0][0] if rows else None

    @router.post("/admin/users/{user_id}/reset_password")
    def reset_password(user_id: int, request: Request):
        """
        @brief  重置口令:本地用户=新一次性口令+首登强改;
                SSO 用户=踢下线(iat 吊销水位,H03 §6)
        """
        admin, error = ctx.require_admin(request)
        if error:
            return error
        target = _target_username(user_id)
        if target is None:
            return JSONResponse({"error": "用户不存在"}, status_code=404)
        user = ctx.auth._get_local_user(target)
        ctx.accounts.revoke_tokens(target)          # 两类用户都先断令牌
        if user["sso_sub"]:
            ctx.audit.append(admin["username"], events.PASSWORD_RESET,
                             {"system": "certvault", "target": target,
                              "sso_kick": True}, "0.0.0.0")
            return {"kicked": target,
                    "note": "SSO 用户无本地口令,已执行踢下线"}
        from gd_crypto import hash_password
        one_time_password = "Gd!" + secrets.token_urlsafe(9)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        ctx.db.execute(
            "UPDATE cv_users SET password_hash = ?, password_changed_at = ?,"
            " must_change_password = 1 WHERE username = ?",
            (hash_password(one_time_password, ctx.suite), now, target))
        ctx.audit.append(admin["username"], events.PASSWORD_RESET,
                         {"system": "certvault", "target": target}, "0.0.0.0")
        return {"reset": target, "one_time_password": one_time_password}

    @router.post("/admin/users/{user_id}/disable")
    def disable_user(user_id: int, request: Request):
        """@brief 停用(逐请求回库校验即刻切断在线会话)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        target = _target_username(user_id)
        if target is None:
            return JSONResponse({"error": "用户不存在"}, status_code=404)
        if target == admin["username"]:
            return JSONResponse({"error": "不能停用自己"}, status_code=400)
        ctx.accounts.set_status(target, "disabled")
        ctx.audit.append(admin["username"], events.USER_DISABLED,
                         {"system": "certvault", "target": target}, "0.0.0.0")
        return {"disabled": target}

    @router.post("/admin/users/{user_id}/enable")
    def enable_user(user_id: int, request: Request):
        """@brief 启用"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        target = _target_username(user_id)
        if target is None:
            return JSONResponse({"error": "用户不存在"}, status_code=404)
        ctx.accounts.set_status(target, "active")
        ctx.audit.append(admin["username"], events.USER_ENABLED,
                         {"system": "certvault", "target": target}, "0.0.0.0")
        return {"enabled": target}

    @router.post("/admin/users/{user_id}/reset_2fa")
    def reset_2fa(user_id: int, request: Request):
        """@brief 管理员重置 2FA(设备丢失自救)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        target = _target_username(user_id)
        if target is None:
            return JSONResponse({"error": "用户不存在"}, status_code=404)
        ctx.auth.reset_2fa(target, admin["username"], "0.0.0.0")
        return {"reset_2fa": target}

    @router.post("/admin/users/kick")
    async def admin_kick(request: Request):
        """@brief 踢下线(兼容里程碑 3 契约:刷新 token_valid_after)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        form = await read_form(request)
        target = form.get("username", "")
        ctx.accounts.revoke_tokens(target)
        ctx.audit.append(admin["username"], events.PASSWORD_RESET,
                         {"system": "certvault", "target": target,
                          "sso_kick": True}, "0.0.0.0")
        return {"kicked": target}

    @router.get("/admin/audit/verify")
    def audit_verify(request: Request):
        """@brief 审计链一键校验(链式哈希,02-B4)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        try:
            count = verify_chain(ctx.db)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=500)
        return {"ok": True, "verified_rows": count}

    @router.get("/admin/audit/export")
    def audit_export(request: Request):
        """@brief 审计 CSV 导出(留导出审计)"""
        admin, error = ctx.require_admin(request)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT id, actor, action, detail, ip, ts FROM audit_logs"
            " ORDER BY id")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "actor", "action", "detail", "ip", "ts"])
        writer.writerows(rows)
        ctx.audit.append(admin["username"], events.DATA_EXPORTED,
                         {"system": "certvault", "rows": len(rows)}, "0.0.0.0")
        return Response(content=buffer.getvalue(), media_type="text/csv")

    return router
