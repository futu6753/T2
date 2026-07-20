# -*- coding: utf-8 -*-
"""
@file    web.py
@brief   三维物联大屏 Web 契约层(L03 §7 / H02-D):
         鉴权矩阵——大屏公开;/admin* 与管理 API 须统一登录(302/401),
         ADMIN_TOKEN 降级为脚本/应急通道;/api/external/* 由签名防护。
         `GET /` 内容协商:浏览器(Accept: text/html)得数据壳大屏,
         API 侧得 JSON {"public": true}(M3 前瞻契约 test_c2_f3d_auth_matrix)。
@author  港电实验室平台组
@date    2026-07-18(M3 骨架)/ 2026-07-19(M6 全功能)
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hmac
import json

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from apps.factory3d import layout as lo
from apps.factory3d import stream
from apps.factory3d.assistant import AssistantEngine
from apps.factory3d.context import F3D_VER, F3dContext
from apps.factory3d.page import render_big_screen
from apps.factory3d.web_data import (
    build_assistant_router,
    build_data_router,
    build_edit_router,
)
from apps.rp_common.accounts import ROLE_ADMIN, ROLE_OPERATOR, RpAccountService
from apps.rp_common.sso_routes import build_sso_router, require_session

SYSTEM = "f3d"
COOKIE_NAME = "gd_f3d_sid"


async def read_json(request: Request) -> dict:
    """@brief 读 JSON 请求体(非法/空体回空 dict,由各路由自行校验)"""
    try:
        payload = json.loads(await request.body() or b"{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def create_app(db, suite, sso, admin_token: str = "", ring=None,
               settings=None, environ=None) -> FastAPI:
    """
    @brief  装配 factory-3d 应用(M6 全功能)
    @param  admin_token 脚本/应急通道令牌(空=禁用;配置 SSO 后人机界面不用它)
    @param  ring        主密钥环(外部注入密钥信封加密;缺省禁用密钥管理)
    @param  settings    统一策略层实例(缺省按 db 新建)
    """
    app = FastAPI(title="港电 三维物联大屏", docs_url=None, redoc_url=None)
    ctx = F3dContext(db, suite, ring=ring, settings=settings, environ=environ)
    engine = AssistantEngine(ctx)
    accounts = RpAccountService(db, suite, table="f3d_users",
                                allowed_roles=(ROLE_ADMIN, ROLE_OPERATOR),
                                default_role=ROLE_OPERATOR)
    cookie = sso.config.cookie_name or COOKIE_NAME
    app.include_router(build_sso_router(sso, accounts, cookie_name=cookie,
                                        cookie_secure=sso.config.cookie_secure))
    app.state.accounts = accounts
    app.state.f3d = ctx

    def _admin_gate(request: Request):
        """
        @brief  管理域闸门:统一登录会话优先;ADMIN_TOKEN 仅作脚本/应急通道
        @return (identity, None) 或 (None, 错误响应)
        """
        user, error = require_session(request, sso, accounts, cookie_name=cookie)
        if user is not None:
            return user["username"], None
        token = request.headers.get("x-admin-token", "")
        if admin_token and token and hmac.compare_digest(token, admin_token):
            return "admin-token-channel", None
        return None, error

    app.include_router(build_data_router(ctx, _admin_gate))
    app.include_router(build_edit_router(ctx, _admin_gate))
    app.include_router(build_assistant_router(ctx, engine, _admin_gate))

    # ---- 公开面 -----------------------------------------------------------
    @app.get("/")
    def big_screen(request: Request):
        """@brief 大屏首页(公开):浏览器得 HTML 数据壳,API 得 JSON 契约"""
        if "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(render_big_screen(
                ctx.settings.get("f3d_site_name"), F3D_VER,
                ctx.settings.get("f3d_min_icon_px")))
        return {"public": True, "system": SYSTEM, "version": F3D_VER,
                "data_rev": ctx.data_rev}

    @app.get("/healthz")
    def healthz():
        """@brief 健康检查:version+设备数+告警计数(L03 §7/§8)"""
        doc, _ = ctx.layouts.get()
        return {"status": "ok", "system": SYSTEM, "version": F3D_VER,
                "devices": lo.count_devices(doc),
                "alarms": ctx.alarms.counts(),
                "sso_enabled": sso.status()["enabled"]}

    @app.get("/api/layout")
    def api_layout():
        """@brief 布局全量(公开只读)"""
        doc, data_rev = ctx.layouts.get()
        return {"layout": doc, "data_rev": data_rev}

    @app.get("/api/devices")
    def api_devices():
        """@brief 设备运行时全量(公开只读)"""
        return {"devices": ctx.simulator.snapshot()}

    @app.get("/api/devices/{device_id}")
    def api_device(device_id: str):
        """@brief 单设备详情(布局字段 + 运行时)"""
        doc, _ = ctx.layouts.get()
        try:
            building, device = lo.find_device(doc, device_id)
        except lo.LayoutError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        runtime = ctx.simulator.runtime.get(device_id, {})
        return {"device": device,
                "building": building["name"] if building else "室外园区",
                "status": runtime.get("status"),
                "metrics": runtime.get("metrics", {}),
                "alarm_state": ctx.alarms.state_of(device_id)}

    @app.get("/api/summary")
    def api_summary():
        """@brief KPI 四枚"""
        return {"kpi": ctx.kpi(), "data_rev": ctx.data_rev,
                "tier": ctx.ladder.tier_name()}

    @app.get("/api/events")
    def api_events():
        """@brief 最近 50 条事件(L03 §7)"""
        return {"events": stream.recent_events(ctx.db)}

    @app.get("/api/alarms")
    def api_alarms():
        """@brief 告警面板 {active, counts, recent}(L03 §7)"""
        return {"active": ctx.alarms.active_list(),
                "counts": ctx.alarms.counts(),
                "recent": ctx.alarms.recent_history()}

    # ---- 管理面 -----------------------------------------------------------
    @app.post("/api/devices/{device_id}/toggle")
    def api_toggle(device_id: str, request: Request):
        """@brief 模拟掉线/恢复(标记手动,模拟器不再自动改;须登录)"""
        identity, error = _admin_gate(request)
        if error:
            return error
        if device_id not in ctx.simulator.runtime:
            return JSONResponse({"detail": "设备不存在"}, status_code=404)
        old, new = ctx.toggle_device(device_id)
        return {"ok": True, "from": old, "to": new, "operator": identity}

    @app.post("/api/alarms/ack")
    async def api_ack(request: Request):
        """@brief 消除告警(alarm_id|device_id|all;须登录)"""
        identity, error = _admin_gate(request)
        if error:
            return error
        body = await read_json(request)
        count = ctx.alarms.ack(alarm_id=body.get("alarm_id"),
                               device_id=body.get("device_id"),
                               ack_all=bool(body.get("all")))
        return {"ok": True, "acked": count, "operator": identity}

    @app.get("/admin/edit")
    def admin_edit(request: Request):
        """@brief 布局编辑器入口(须统一登录;M3 契约)"""
        identity, error = _admin_gate(request)
        if error:
            return error
        return {"editor": "交互式编辑台", "version": F3D_VER,
                "session_active": ctx.edit_session_active,
                "operator": identity}

    @app.get("/api/admin/layout")
    def admin_layout(request: Request):
        """@brief 管理布局视图(401 语义 + token 应急通道锚点;M3 契约)"""
        identity, error = _admin_gate(request)
        if error:
            return error
        _, data_rev = ctx.layouts.get()
        return {"layout_version": data_rev, "operator": identity}

    # ---- 外部注入(listener 侧签名防护,L03 §7 / H02-D1) ------------------
    @app.post("/api/external/{device_id}")
    async def api_external(device_id: str, request: Request):
        """@brief 设备注入 {status, metrics}:HMAC 校验失败一律 401「鉴权失败」"""
        body = await request.body()
        verified = ctx.verify_external(
            request.headers.get("x-gd-key-id", ""),
            request.headers.get("x-gd-timestamp", ""),
            request.headers.get("x-gd-signature", ""), body)
        if not verified:
            ctx.audit.append("external", "login_failed",
                             {"channel": "f3d_external"}, "0.0.0.0")
            return JSONResponse({"detail": "鉴权失败"}, status_code=401)
        if device_id not in ctx.simulator.runtime:
            return JSONResponse({"detail": "设备不存在"}, status_code=404)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return JSONResponse({"detail": "请求体须为 JSON"}, status_code=400)
        old = ctx.simulator.runtime[device_id]["status"]
        if payload.get("metrics"):
            ctx.simulator.inject(device_id, metrics=payload["metrics"])
        new = old
        status = payload.get("status")
        if status in ("online", "offline") and status != old:
            _, new = ctx.apply_status(device_id, status, "external")
        return {"ok": True, "from": old, "to": new}

    # ---- 实时通道 ---------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """@brief WS 单向推送:snapshot→周期 update(fps 回报喂降级阶梯)"""
        await stream.ws_session(websocket, ctx)

    return app
