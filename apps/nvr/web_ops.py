# -*- coding: utf-8 -*-
"""
@file    web_ops.py
@brief   运维区(L04 §4):报告三路由、/metrics(登录会话或 Bearer 常数
         时间)、对外只读 /public/v1(HMAC 五行待签串,失败一律 401
         「鉴权失败」)、推送 /ingest/{token}(GET|POST=/api/ingest 别名,
         空请求=心跳)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.nvr.exposition import metrics_token_ok, render_metrics

NVR_ROLES = ("admin", "operator", "auditor")


def build_ops_router(ctx) -> "APIRouter":
    """@brief 运维区路由(NvrContext 注入)"""
    router = APIRouter()

    # ---- 报告 -----------------------------------------------------------
    @router.post("/api/reports/generate")
    async def generate_report(request: Request):
        """@brief 手动生成报告 {period_days}"""
        user, error = ctx.guard(request, ("admin", "operator"))
        if error:
            return error
        from apps.nvr.web import read_json
        payload = await read_json(request)
        report = ctx.reports.generate(int(payload.get("period_days", 7)))
        return report

    @router.get("/api/reports")
    def list_reports(request: Request):
        """@brief 报告列表"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return {"reports": ctx.reports.list()}

    @router.get("/api/reports/latest")
    def latest_report(request: Request):
        """@brief 最新报告(含事实层)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        report = ctx.reports.latest()
        if report is None:
            return JSONResponse({"error": "尚无报告"}, status_code=404)
        return report

    # ---- Prometheus -----------------------------------------------------
    @router.get("/metrics")
    def metrics(request: Request):
        """@brief 0.0.4 文本(需登录;或 Bearer token 常数时间比较)"""
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer ") and ctx.metrics_token:
            if not metrics_token_ok(authorization[len("Bearer "):],
                                    ctx.metrics_token):
                return JSONResponse({"error": "鉴权失败"}, status_code=401)
        else:
            user, error = ctx.guard(request, NVR_ROLES)
            if error:
                return error
        options = ctx.options
        return PlainTextResponse(render_metrics(
            ctx.db, per_device=options.get("metrics_per_device", True),
            per_channel=options.get("metrics_per_channel", True),
            include_disabled=options.get("metrics_include_disabled", False)),
            media_type="text/plain; version=0.0.4; charset=utf-8")

    # ---- 对外只读 /public/v1(HMAC) ------------------------------------
    def _public_gate(request: Request, path: str, body: bytes = b""):
        """@brief HMAC 验签闸门(失败一律 401 不区分原因)"""
        query = dict(request.query_params)
        headers = {key.lower(): value
                   for key, value in request.headers.items()}
        if not ctx.public_guard.verify(request.method, path, query,
                                       headers, body):
            return JSONResponse({"detail": "鉴权失败"}, status_code=401)
        return None

    @router.get("/public/v1/status/overview")
    def public_overview(request: Request):
        """@brief 对外总览(脱敏:仅计数)"""
        denied = _public_gate(request, "/public/v1/status/overview")
        if denied is not None:
            return denied
        devices = ctx.devices.list(enabled=True)
        by_status = {}
        for device in devices:
            status = ctx.devices.state_of(device["id"])["status"]
            by_status[status] = by_status.get(status, 0) + 1
        firing = len(ctx.alerts.list_alerts(state="firing", limit=500))
        return {"total": len(devices), "by_status": by_status,
                "active_alerts": firing}

    @router.get("/public/v1/status/devices")
    def public_devices(request: Request, region: str = None,
                       station: str = None):
        """@brief 对外设备状态(脱敏:无 host/username/token)"""
        denied = _public_gate(request, "/public/v1/status/devices")
        if denied is not None:
            return denied
        result = []
        for device in ctx.devices.list(region=region, station=station):
            state = ctx.devices.state_of(device["id"])
            result.append({"name": device["name"], "kind": device["kind"],
                           "region": device["region"],
                           "station": device["station"],
                           "status": state["status"],
                           "since": state["since"]})
        return {"devices": result}

    @router.get("/public/v1/alerts")
    def public_alerts(request: Request, state: str = None,
                      limit: int = 50):
        """@brief 对外告警(脱敏)"""
        denied = _public_gate(request, "/public/v1/alerts")
        if denied is not None:
            return denied
        alerts = ctx.alerts.list_alerts(state=state, limit=limit)
        return {"alerts": [
            {"scope": alert["scope"], "state": alert["state"],
             "trigger_status": alert["trigger_status"],
             "started_at": alert["started_at"],
             "duration_seconds": alert["duration_seconds"]}
            for alert in alerts]}

    # ---- 推送接收 -------------------------------------------------------
    @router.post("/ingest/{token}")
    @router.get("/ingest/{token}")
    @router.post("/api/ingest/{token}")
    @router.get("/api/ingest/{token}")
    async def ingest(token: str, request: Request):
        """@brief 推送上报(空请求=心跳;应答契约四字段)"""
        payload = {}
        if request.method == "POST":
            from apps.nvr.web import read_json
            payload = await read_json(request)
        result = ctx.ingest.handle(token, payload)
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return result

    return router
