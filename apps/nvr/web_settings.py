# -*- coding: utf-8 -*-
"""
@file    web_settings.py
@brief   补齐 L04 §4 三区:
         ①/api/settings(C3 schema 驱动:env>后台>文件>默认、null=删除
         覆盖、未知键报错、cron 保存时校验、env 锁定拒改、restart 标记)
         与 /api/settings/reset;
         ②/api/notifications/channels(渠道就绪度,不回显密钥);
         ③/api/logs/events(状态跃迁∪通道跃迁∪告警启停 UNION 下推,
         过滤 region/station/type/时间窗)与 /api/logs/stations。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gd_common.errors import ConfigError, PolicyValidationError
from gd_policy.schema import SCHEMA_BY_KEY
from apps.nvr.cron import validate_cron

NVR_ROLES = ("admin", "operator", "auditor")
_EVENT_TYPES = ("status_change", "channel_change", "alert_fired",
                "alert_resolved")


def build_settings_router(ctx) -> "APIRouter":
    """@brief 设置/渠道就绪/日志路由(NvrContext 注入;需 ctx.settings)"""
    router = APIRouter()

    # ---- 设置区(02-C3) ------------------------------------------------
    def _nvr_params():
        """@brief 统一 schema 中的 nvr_ 分区"""
        return [param for key, param in SCHEMA_BY_KEY.items()
                if key.startswith("nvr_")]

    @router.get("/api/settings")
    def get_settings(request: Request):
        """@brief 分区展示:值+来源层+env 锁定+restart 标记(密钥类不出现)"""
        user, error = ctx.guard(request, ("admin",))
        if error:
            return error
        if ctx.settings is None:
            return JSONResponse({"error": "设置服务未装配"}, status_code=503)
        sections = {}
        for param in _nvr_params():
            value, source = ctx.settings.get_with_source(param.key)
            sections.setdefault(param.section, []).append({
                "key": param.key, "label": param.label, "type": param.type,
                "value": value, "default": param.default, "source": source,
                "choices": list(param.choices), "unit": param.unit,
                "restart": param.restart,
                "env_locked": source == "env", "help": param.help})
        return {"sections": sections,
                "version": ctx.settings.version()}

    @router.put("/api/settings")
    async def put_settings(request: Request):
        """@brief 批量写覆盖层(values 中 null=删除覆盖恢复下层;逐键校验)"""
        user, error = ctx.guard(request, ("admin",))
        if error:
            return error
        if ctx.settings is None:
            return JSONResponse({"error": "设置服务未装配"}, status_code=503)
        from apps.nvr.web import read_json
        payload = await read_json(request)
        values = payload.get("values", {})
        applied, errors = {}, {}
        for key, raw in values.items():
            if not key.startswith("nvr_") or key not in SCHEMA_BY_KEY:
                errors[key] = "未知配置键"
                continue
            try:
                if key == "nvr_report_cron" and raw is not None:
                    validate_cron(str(raw))          # 保存时校验(L04 §6)
                applied[key] = ctx.settings.set_override(
                    key, raw, user["username"], "0.0.0.0",
                    audit_writer=ctx.audit)
            except (PolicyValidationError, ConfigError) as exc:
                errors[key] = str(exc)
        status = 200 if not errors else 400
        return JSONResponse({"applied": applied, "errors": errors,
                             "version": ctx.settings.version()},
                            status_code=status)

    @router.post("/api/settings/reset")
    def reset_settings(request: Request):
        """@brief 删除全部 nvr_ 覆盖层(恢复文件/默认)"""
        user, error = ctx.guard(request, ("admin",))
        if error:
            return error
        if ctx.settings is None:
            return JSONResponse({"error": "设置服务未装配"}, status_code=503)
        cleared = []
        for param in _nvr_params():
            try:
                ctx.settings.set_override(param.key, None, user["username"],
                                          "0.0.0.0", audit_writer=ctx.audit)
                cleared.append(param.key)
            except PolicyValidationError:
                pass                                  # env 锁定项跳过
        return {"reset": cleared}

    # ---- 渠道就绪度 -----------------------------------------------------
    @router.get("/api/notifications/channels")
    def notification_channels(request: Request):
        """@brief 渠道就绪度(不回显密钥,L04 §4)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        channels = []
        for channel_id, channel in ctx.dispatcher.channels().items():
            channels.append({"channel": channel_id,
                             "ready": channel.ready(),
                             "detail": channel.describe()})
        return {"channels": channels}

    # ---- 日志区(UNION 下推) -------------------------------------------
    @router.get("/api/logs/events")
    def logs_events(request: Request, region: str = None, station: str = None,
                    type: str = None, since: str = None, until: str = None,
                    limit: int = 100):
        """@brief 统一事件流:时间线 ∪ 告警启停(SQL UNION 下推)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        wanted = [t.strip() for t in (type or "").split(",") if t.strip()] \
            or list(_EVENT_TYPES)
        bad = [t for t in wanted if t not in _EVENT_TYPES]
        if bad:
            return JSONResponse(
                {"error": f"未知事件类型: {','.join(bad)}"}, status_code=400)

        def clauses(time_col):
            conditions, params = [], []
            if region:
                conditions.append("d.region = ?")
                params.append(region)
            if station:
                conditions.append("d.station = ?")
                params.append(station)
            if since:
                conditions.append(f"{time_col} >= ?")
                params.append(since)
            if until:
                conditions.append(f"{time_col} <= ?")
                params.append(until)
            return (" AND " + " AND ".join(conditions) if conditions else "",
                    params)

        timeline_where, timeline_params = clauses("t.occurred_at")
        fired_where, fired_params = clauses("a.started_at")
        resolved_where, resolved_params = clauses("a.resolved_at")
        sql = (
            "SELECT * FROM ("
            " SELECT t.event_type AS event_type, t.device_id, d.name,"
            "  d.region, d.station, t.from_status, t.to_status, t.detail,"
            "  t.occurred_at AS at FROM nvr_timeline t"
            "  JOIN nvr_devices d ON d.id = t.device_id"
            f"  WHERE 1=1{timeline_where}"
            " UNION ALL"
            " SELECT 'alert_fired', a.device_id, d.name, d.region, d.station,"
            "  '', a.trigger_status, a.detail, a.started_at FROM nvr_alerts a"
            "  JOIN nvr_devices d ON d.id = a.device_id"
            f"  WHERE 1=1{fired_where}"
            " UNION ALL"
            " SELECT 'alert_resolved', a.device_id, d.name, d.region,"
            "  d.station, a.trigger_status, 'resolved', a.detail,"
            "  a.resolved_at FROM nvr_alerts a"
            "  JOIN nvr_devices d ON d.id = a.device_id"
            f"  WHERE a.resolved_at IS NOT NULL{resolved_where}"
            ") ORDER BY at DESC LIMIT ?")
        params = tuple(timeline_params + fired_params + resolved_params
                       + [min(limit, 500)])
        rows = ctx.db.query(sql, params)
        events = [dict(zip(("event_type", "device_id", "device_name",
                            "region", "station", "from_status", "to_status",
                            "detail", "at"), row)) for row in rows
                  if row[0] in wanted]
        return {"events": events}

    @router.get("/api/logs/stations")
    def logs_stations(request: Request):
        """@brief 场站清单(过滤器数据源)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT DISTINCT region, station FROM nvr_devices"
            " WHERE station != '' ORDER BY region, station")
        return {"stations": [{"region": row[0], "station": row[1]}
                             for row in rows]}

    return router
