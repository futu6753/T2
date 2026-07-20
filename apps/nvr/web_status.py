# -*- coding: utf-8 -*-
"""
@file    web_status.py
@brief   状态查询区(L04 §4):/api/status/overview(summary+by_kind+
         active_alerts+patrol)、/api/status/devices(附 active_alert 摘要与
         通道汇总)、检测明细/时间线/状态变化、告警查询、通知留痕、通道查询。
         全区 auditor 起可读。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.nvr.alerts import SCOPE_CHANNEL, SCOPE_DEVICE

NVR_ROLES = ("admin", "operator", "auditor")


def build_status_router(ctx) -> "APIRouter":
    """@brief 状态区路由(NvrContext 注入)"""
    router = APIRouter()

    @router.get("/api/status/overview")
    def overview(request: Request):
        """@brief 总览(summary 三类+unchecked/by_status/by_kind/告警/巡检)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        devices = ctx.devices.list(enabled=True)
        by_status, by_kind = {}, {"nvr": {}, "push": {}}
        for device in devices:
            state = ctx.devices.state_of(device["id"])
            status = state["status"]
            by_status[status] = by_status.get(status, 0) + 1
            bucket = by_kind[device["kind"]]
            bucket[status] = bucket.get(status, 0) + 1
        firing = ctx.alerts.list_alerts(state="firing", limit=500)
        alerts_by_scope = {}
        for alert in firing:
            alerts_by_scope[alert["scope"]] = \
                alerts_by_scope.get(alert["scope"], 0) + 1
        abnormal = sum(count for status, count in by_status.items()
                       if status not in ("online", "offline", "unchecked"))
        return {"summary": {"online": by_status.get("online", 0),
                            "offline": by_status.get("offline", 0),
                            "abnormal": abnormal,
                            "unchecked": by_status.get("unchecked", 0)},
                "by_status": by_status, "by_kind": by_kind,
                "active_alerts": len(firing),
                "alerts_by_scope": alerts_by_scope,
                "patrol": {"running": ctx.patrol.running,
                           "next_run_at": ctx.patrol.next_run_at,
                           "last_cycle": ctx.patrol.last_cycle}}

    @router.get("/api/status/devices")
    def status_devices(request: Request, status: str = None,
                       region: str = None):
        """@brief 设备状态列表(附 active_alert 摘要与通道汇总)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        result = []
        for device in ctx.devices.list(region=region):
            state = ctx.devices.state_of(device["id"])
            if status and state["status"] != status:
                continue
            active = ctx.alerts.active_alert(device["id"], SCOPE_DEVICE) \
                or ctx.alerts.active_alert(device["id"], SCOPE_CHANNEL)
            channel_info = ctx.channels.list(device_id=device["id"])
            result.append({**device, "state": state,
                           "active_alert": active and {
                               "scope": active["scope"],
                               "trigger_status": active["trigger_status"],
                               "duration_seconds":
                                   active["duration_seconds"]},
                           "channels": channel_info["summary"]})
        return {"devices": result}

    @router.get("/api/devices/{device_id}/results")
    def device_results(device_id: int, request: Request, status: str = None,
                       source: str = None, since: str = None,
                       until: str = None, limit: int = 100):
        """@brief 检测明细(过滤 status/source/时间窗 ISO8601 含边界)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        conditions, params = ["device_id = ?"], [device_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if since:
            conditions.append("checked_at >= ?")
            params.append(since)
        if until:
            conditions.append("checked_at <= ?")
            params.append(until)
        params.append(min(limit, 500))
        rows = ctx.db.query(
            "SELECT id, status, source, detail, latency_ms, checked_at"
            f" FROM nvr_check_results WHERE {' AND '.join(conditions)}"
            " ORDER BY id DESC LIMIT ?", tuple(params))
        return {"results": [dict(zip(
            ("id", "status", "source", "detail", "latency_ms", "checked_at"),
            row)) for row in rows]}

    @router.get("/api/devices/{device_id}/timeline")
    def device_timeline(device_id: int, request: Request, limit: int = 100):
        """@brief 设备时间线(状态跃迁+通道跃迁)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT id, event_type, channel_no, from_status, to_status,"
            " detail, occurred_at FROM nvr_timeline WHERE device_id = ?"
            " ORDER BY id DESC LIMIT ?", (device_id, min(limit, 500)))
        return {"timeline": [dict(zip(
            ("id", "event_type", "channel_no", "from_status", "to_status",
             "detail", "occurred_at"), row)) for row in rows]}

    @router.get("/api/status/changes")
    def status_changes(request: Request, device_id: int = None,
                       since: str = None, until: str = None,
                       limit: int = 100):
        """@brief 全局状态变化流"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        conditions, params = ["event_type = 'status_change'"], []
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)
        if since:
            conditions.append("occurred_at >= ?")
            params.append(since)
        if until:
            conditions.append("occurred_at <= ?")
            params.append(until)
        params.append(min(limit, 500))
        rows = ctx.db.query(
            "SELECT id, device_id, from_status, to_status, detail,"
            f" occurred_at FROM nvr_timeline WHERE {' AND '.join(conditions)}"
            " ORDER BY id DESC LIMIT ?", tuple(params))
        return {"changes": [dict(zip(
            ("id", "device_id", "from_status", "to_status", "detail",
             "occurred_at"), row)) for row in rows]}

    @router.get("/api/alerts")
    def list_alerts(request: Request, state: str = None, scope: str = None,
                    device_id: int = None, limit: int = 100):
        """@brief 告警列表(附设备摘要与 active_total)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        alerts = ctx.alerts.list_alerts(state=state, scope=scope,
                                        device_id=device_id, limit=limit)
        for alert in alerts:
            device = ctx.devices.get(alert["device_id"])
            alert["device"] = device and {"name": device["name"],
                                          "region": device["region"],
                                          "station": device["station"]}
        active_total = len(ctx.alerts.list_alerts(state="firing", limit=500))
        return {"alerts": alerts, "active_total": active_total}

    @router.get("/api/alerts/{alert_id}")
    def alert_detail(alert_id: int, request: Request):
        """@brief 单条告警"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT id, device_id, scope, state, trigger_status, detail,"
            " started_at, resolved_at, duration_seconds FROM nvr_alerts"
            " WHERE id = ?", (alert_id,))
        if not rows:
            return JSONResponse({"error": "告警不存在"}, status_code=404)
        return dict(zip(("id", "device_id", "scope", "state",
                         "trigger_status", "detail", "started_at",
                         "resolved_at", "duration_seconds"), rows[0]))

    @router.get("/api/notifications")
    def list_notifications(request: Request, state: str = None,
                           alert_id: int = None):
        """@brief 通知留痕"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return {"notifications": ctx.dispatcher.list_notifications(
            state=state, alert_id=alert_id)}

    @router.get("/api/channels")
    def list_channels(request: Request, device_id: int = None,
                      status: str = None, include_removed: bool = False):
        """@brief 通道列表(含 summary)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return ctx.channels.list(device_id=device_id, status=status,
                                 include_removed=include_removed)

    @router.get("/api/devices/{device_id}/channels")
    def device_channels(device_id: int, request: Request):
        """@brief 单设备通道"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return ctx.channels.list(device_id=device_id)

    @router.get("/api/channels/changes")
    def channel_changes(request: Request, device_id: int = None,
                        limit: int = 100):
        """@brief 通道跃迁流(统一时间线 channel_change)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        conditions, params = ["event_type = 'channel_change'"], []
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)
        params.append(min(limit, 500))
        rows = ctx.db.query(
            "SELECT id, device_id, channel_no, from_status, to_status,"
            f" occurred_at FROM nvr_timeline WHERE {' AND '.join(conditions)}"
            " ORDER BY id DESC LIMIT ?", tuple(params))
        return {"changes": [dict(zip(
            ("id", "device_id", "channel_no", "from_status", "to_status",
             "occurred_at"), row)) for row in rows]}

    return router
