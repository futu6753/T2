# -*- coding: utf-8 -*-
"""
@file    web_data.py
@brief   数据管理台 /api/data/*(设置与结构 CRUD、外部注入密钥)、交互式
         编辑台 /api/edit/*(会话锁守卫,未启会话 409)、AI 助手
         /api/assistant/*(preview → 确认 → 原子执行)三组路由(L03 §3/§4/§5)。
         结构变更统一走 _mutate:校验失败 400 人话报错、成功 data_rev+1
         并记 layout 事件(大屏「布局已更新」芯片依据)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gd_policy.service import ConfigError, PolicyValidationError

from apps.factory3d import layout as lo
from apps.factory3d import stream


async def _json(request: Request) -> dict:
    """@brief 读 JSON 体(非法回空 dict)"""
    try:
        payload = json.loads(await request.body() or b"{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _mutate(ctx, mutator, structural: bool = True):
    """@brief 结构变更统一入口:校验→落库→运行时联动→layout 事件"""
    try:
        doc, data_rev = ctx.layouts.mutate(mutator, structural=structural)
    except lo.LayoutError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    ctx.layout_changed(doc, data_rev)
    if structural:
        stream.record_event(ctx.db, "layout", detail=f"rev={data_rev}")
    return {"ok": True, "data_rev": data_rev}


def build_data_router(ctx, admin_gate) -> APIRouter:
    """@brief 数据管理台路由(全部须登录/应急通道)"""
    router = APIRouter(prefix="/api/data")

    def _guard(request: Request):
        return admin_gate(request)

    @router.get("/settings")
    def get_settings(request: Request):
        """@brief F3D 参数视图(值/来源/元数据,schema 驱动)"""
        identity, error = _guard(request)
        if error:
            return error
        items = [item for item in ctx.settings.describe_all()
                 if item["key"].startswith("f3d_")]
        return {"settings": items, "version": ctx.settings.version()}

    @router.patch("/settings")
    async def patch_settings(request: Request):
        """@brief 批量改 F3D 参数:逐键校验,任一非法则本次全部不写(400)"""
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        values = body.get("values", body)
        if not isinstance(values, dict) or not values:
            return JSONResponse({"detail": "请求体须为参数键值对象"},
                                status_code=400)
        errors = {}
        for key in values:
            if not isinstance(key, str) or not key.startswith("f3d_"):
                errors[str(key)] = "仅允许修改 f3d_ 前缀参数"
        if not errors:
            staged = {}
            for key, value in values.items():
                try:
                    ctx.settings.get_with_source(key)
                    staged[key] = value
                except ConfigError as exc:
                    errors[key] = str(exc)
        if errors:
            return JSONResponse({"detail": "参数校验失败", "errors": errors},
                                status_code=400)
        for key, value in staged.items():
            try:
                ctx.settings.set_override(key, value, identity, "0.0.0.0",
                                          audit_writer=ctx.audit)
            except (PolicyValidationError, ConfigError, ValueError) as exc:
                errors[key] = str(exc)
        if errors:
            return JSONResponse({"detail": "参数校验失败", "errors": errors},
                                status_code=400)
        return {"ok": True, "version": ctx.settings.version()}

    async def _patch_one(request: Request, key: str, body_key: str,
                         label: str):
        """@brief 单参数便捷面板(site/alarm/display/ai 共用)"""
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        if body_key not in body:
            return JSONResponse({"detail": f"缺少 {body_key}"}, status_code=400)
        try:
            value = ctx.settings.set_override(key, body[body_key], identity,
                                              "0.0.0.0",
                                              audit_writer=ctx.audit)
        except (PolicyValidationError, ConfigError, ValueError) as exc:
            return JSONResponse({"detail": f"{label}校验失败: {exc}"},
                                status_code=400)
        return {"ok": True, "key": key, "value": value}

    @router.patch("/site")
    async def patch_site(request: Request):
        """@brief 站点名(大屏渲染前转义)"""
        return await _patch_one(request, "f3d_site_name", "name", "站点名")

    @router.patch("/alarm")
    async def patch_alarm(request: Request):
        """@brief 告警延时分钟(热生效)"""
        return await _patch_one(request, "f3d_alarm_delay_minutes",
                                "delay_min", "告警延时")

    @router.patch("/display")
    async def patch_display(request: Request):
        """@brief 图钉最小像素"""
        return await _patch_one(request, "f3d_min_icon_px", "min_icon_px",
                                "显示设置")

    @router.get("/ai")
    def get_ai(request: Request):
        """@brief AI 助手配置视图(无密钥字段可回显,L03 §3.6 密钥不回显)"""
        identity, error = _guard(request)
        if error:
            return error
        return {"enabled": ctx.settings.get("f3d_assistant_enabled")}

    @router.patch("/ai")
    async def patch_ai(request: Request):
        """@brief AI 助手开关"""
        return await _patch_one(request, "f3d_assistant_enabled", "enabled",
                                "AI 配置")

    # ---- 结构 CRUD ----------------------------------------------------
    @router.post("/zones")
    async def add_zone(request: Request):
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.add_zone(doc, body.get("name", "")))

    @router.patch("/zones/{zone_id}")
    async def patch_zone(zone_id: str, request: Request):
        """@brief 场区改名/透视角/旋转中心"""
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)

        def apply(doc):
            zone = lo.find_zone(doc, zone_id)
            if "name" in body:
                if not body["name"] or len(body["name"]) > 40:
                    raise lo.LayoutError("场区名称须为 1~40 字")
                zone["name"] = body["name"]
            if "elev" in body or "theta" in body or body.get("follow"):
                lo.set_zone_focus(doc, zone_id, elev=body.get("elev"),
                                  theta=None if body.get("follow")
                                  else body.get("theta", "keep"))
            if "dx" in body or "dz" in body or body.get("reset_center"):
                lo.set_zone_center(doc, zone_id, dx=body.get("dx"),
                                   dz=body.get("dz"),
                                   reset=bool(body.get("reset_center")))
        return _mutate(ctx, apply, structural=False)

    @router.delete("/zones/{zone_id}")
    def delete_zone(zone_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        return _mutate(ctx, lambda doc: lo.remove_zone(doc, zone_id))

    @router.post("/zones/{zone_id}/buildings")
    async def add_building(zone_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.add_building(
            doc, zone_id, body.get("name", ""),
            btype=body.get("type", "warehouse")))

    @router.patch("/buildings/{building_id}")
    async def patch_building(building_id: str, request: Request):
        """@brief 楼宇改名/类型/外观模型/移动"""
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)

        def apply(doc):
            if "name" in body:
                lo.rename_building(doc, building_id, body["name"])
            if "type" in body:
                lo.set_building_type(doc, building_id, body["type"])
            if "model" in body:
                lo.set_building_model(doc, building_id, body["model"])
            if "dx" in body and "dz" in body:
                lo.move_building(doc, building_id, body["dx"], body["dz"])
        return _mutate(ctx, apply)

    @router.delete("/buildings/{building_id}")
    def delete_building(building_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        return _mutate(ctx, lambda doc: lo.remove_building(doc, building_id))

    @router.post("/buildings/{building_id}/devices")
    async def add_device(building_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.add_device(
            doc, body.get("name", ""), body.get("type", ""),
            building_id=building_id, room=body.get("room", ""),
            ip=body.get("ip", ""), pos=body.get("pos")))

    @router.post("/outdoor/devices")
    async def add_outdoor(request: Request):
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.add_device(
            doc, body.get("name", ""), body.get("type", ""), building_id=None,
            room=body.get("room", ""), ip=body.get("ip", ""),
            pos=body.get("pos")))

    @router.post("/buildings/{building_id}/reset-devices")
    def reset_devices(building_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        return _mutate(ctx, lambda doc: lo.reset_building_devices(
            doc, building_id))

    @router.patch("/devices/{device_id}")
    async def patch_device(device_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.patch_device(doc, device_id, body))

    @router.delete("/devices/{device_id}")
    def delete_device(device_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        return _mutate(ctx, lambda doc: lo.remove_device(doc, device_id))

    @router.post("/reset")
    def reset_layout(request: Request):
        identity, error = _guard(request)
        if error:
            return error

        def apply(doc):
            doc.clear()
            doc.update(lo.default_layout())
        return _mutate(ctx, apply)

    # ---- 外部注入密钥 ---------------------------------------------------
    @router.post("/external-keys")
    def create_key(request: Request):
        """@brief 创建注入密钥(明文仅此一次)"""
        identity, error = _guard(request)
        if error:
            return error
        try:
            created = ctx.create_external_key()
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        ctx.audit.append(identity, "settings_changed",
                         {"key": "f3d_external_key", "change": "created",
                          "key_id": created["key_id"]}, "0.0.0.0")
        return {"ok": True, "key_id": created["key_id"],
                "secret": created["secret"],
                "note": "明文仅展示一次,请立即保存"}

    @router.get("/external-keys")
    def list_keys(request: Request):
        identity, error = _guard(request)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT key_id, enabled, created_at, revoked_at"
            " FROM f3d_external_keys ORDER BY id")
        return {"keys": [{"key_id": row[0], "enabled": bool(row[1]),
                          "created_at": row[2], "revoked_at": row[3]}
                         for row in rows]}

    @router.post("/external-keys/{key_id}/revoke")
    def revoke_key(key_id: str, request: Request):
        identity, error = _guard(request)
        if error:
            return error
        if not ctx.revoke_external_key(key_id):
            return JSONResponse({"detail": "密钥不存在或已吊销"},
                                status_code=404)
        return {"ok": True}

    return router


def build_edit_router(ctx, admin_gate) -> APIRouter:
    """@brief 交互式编辑台路由(会话锁:未启会话一律 409,L03 §4)"""
    router = APIRouter(prefix="/api/edit")

    def _session_guard(request: Request):
        identity, error = admin_gate(request)
        if error:
            return None, error
        if not ctx.edit_session_active:
            return None, JSONResponse(
                {"detail": "交互式编辑会话未启动(session_locked)"},
                status_code=409)
        return identity, None

    @router.get("/session")
    def session_status(request: Request):
        identity, error = admin_gate(request)
        if error:
            return error
        return {"active": ctx.edit_session_active}

    @router.post("/session")
    async def session_switch(request: Request):
        """@brief 启动/关闭编辑会话"""
        identity, error = admin_gate(request)
        if error:
            return error
        body = await _json(request)
        ctx.edit_session_active = bool(body.get("active"))
        return {"ok": True, "active": ctx.edit_session_active}

    @router.post("/buildings/{building_id}/move")
    async def move_building(building_id: str, request: Request):
        """@brief 拖拽落点:夹取+重叠校验,违规 400(前端回弹)"""
        identity, error = _session_guard(request)
        if error:
            return error
        body = await _json(request)
        if "dx" not in body or "dz" not in body:
            return JSONResponse({"detail": "缺少 dx/dz"}, status_code=400)
        return _mutate(ctx, lambda doc: lo.move_building(
            doc, building_id, body["dx"], body["dz"]))

    @router.post("/home/from-view")
    async def home_from_view(request: Request):
        """@brief 以当前视角设为默认视角(L03 §4)"""
        identity, error = _session_guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.set_home(
            doc, target=body.get("target"), radius=body.get("radius", "keep"),
            elev=body.get("elev"), theta=body.get("theta")), structural=False)

    @router.post("/zones/{zone_id}/focus-from-view")
    async def focus_from_view(zone_id: str, request: Request):
        identity, error = _session_guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.set_zone_focus(
            doc, zone_id, elev=body.get("elev"),
            theta=body.get("theta", "keep")), structural=False)

    @router.post("/zones/{zone_id}/center")
    async def zone_center(zone_id: str, request: Request):
        identity, error = _session_guard(request)
        if error:
            return error
        body = await _json(request)
        return _mutate(ctx, lambda doc: lo.set_zone_center(
            doc, zone_id, dx=body.get("dx"), dz=body.get("dz"),
            reset=bool(body.get("reset"))), structural=False)

    return router


def build_assistant_router(ctx, engine, admin_gate) -> APIRouter:
    """@brief AI 助手路由:preview(dry-run)→ execute(tx_id 确认)→ 日志"""
    router = APIRouter(prefix="/api/assistant")

    @router.post("/{scope}/preview")
    async def preview(scope: str, request: Request):
        """@brief 提交模型回复文本,抽取动作并 dry-run(零落库)"""
        identity, error = admin_gate(request)
        if error:
            return error
        body = await _json(request)
        result = engine.preview(body.get("text", ""), scope, identity)
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    @router.post("/{scope}/execute")
    async def execute(scope: str, request: Request):
        """@brief 用户确认后按 tx_id 原子执行"""
        identity, error = admin_gate(request)
        if error:
            return error
        body = await _json(request)
        result = engine.execute_pending(body.get("tx_id", ""), identity)
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    @router.get("/log")
    def log(request: Request):
        """@brief 事务日志(有界留存)"""
        identity, error = admin_gate(request)
        if error:
            return error
        return {"log": engine.tx_log()}

    return router
