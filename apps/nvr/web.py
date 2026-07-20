# -*- coding: utf-8 -*-
"""
@file    web.py
@brief   nvr-monitor 装配中心(L04 §4):NvrContext 服务容器 + SSO 统一登录
         (三角色 RBAC:admin/operator/auditor)+ 设备 CRUD 与巡检路由;
         状态/告警/通知区见 web_status.py,报告/指标/对外/推送/设置区见
         web_ops.py。检测探针可注入(生产 ISAPI,测试 fake)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gd_common.errors import PolicyValidationError
from gd_storage.audit import AuditWriter
from apps.nvr.alerts import AlertEngine
from apps.nvr.checker import DeviceChecker
from apps.nvr.debounce import ALL_MODES, DebouncePolicy, MODE_CONSECUTIVE
from apps.nvr.devices import DeviceService
from apps.nvr.dispatch import Dispatcher
from apps.nvr.exposition import PublicApiGuard
from apps.nvr.ingest import ChannelService, PushIngest
from apps.nvr.patrol import PatrolService
from apps.nvr.report import ReportService
from apps.rp_common.accounts import RpAccountService
from apps.rp_common.sso_routes import (
    build_sso_router, require_role, require_session,
)

SYSTEM = "nvr"
COOKIE_NAME = "gd_nvr_sid"
NVR_ROLES = ("admin", "operator", "auditor")


class NvrContext:
    """nvr 服务容器(路由模块共享)。"""

    def __init__(self, db, ring, suite, sso, options: dict):
        """@brief 装配全部服务(options 见 create_app)"""
        self.db = db
        self.ring = ring
        self.suite = suite
        self.sso = sso
        self.cookie_name = sso.config.cookie_name or COOKIE_NAME
        self.audit = AuditWriter(db, suite)
        self.accounts = RpAccountService(db, suite, table="nvr_users",
                                         allowed_roles=NVR_ROLES,
                                         default_role="auditor")
        self.devices = DeviceService(db, ring, suite)
        self.channels = ChannelService(db)
        self.policy = DebouncePolicy(
            options.get("debounce_mode", MODE_CONSECUTIVE),
            consecutive_failures=options.get("consecutive_failures", 3),
            offline_duration_seconds=options.get("offline_duration_seconds",
                                                 300))
        self.dispatcher = Dispatcher(
            db, options.get("channels", []),
            max_attempts=options.get("retry_max_attempts", 3),
            backoff_seconds=options.get("retry_backoff_seconds", 60))
        self.alerts = AlertEngine(
            db, self.devices, self.policy, dispatcher=self.dispatcher,
            recovery_notice=options.get("recovery_notice", True),
            channel_alerts=options.get("channel_alerts", True))
        checker_factory = options.get("checker_factory") \
            or self._default_checker_factory(options)
        self.patrol = PatrolService(
            self.devices, checker_factory, self.alerts,
            channels_service=self.channels,
            concurrency=options.get("concurrency", 10),
            retention_days=options.get("retention_days", 90),
            master_key_ready=options.get("master_key_ready", lambda: True))
        self.ingest = PushIngest(db, self.devices, self.alerts)
        self.reports = ReportService(
            db, api_key=options.get("anthropic_api_key", ""),
            model=options.get("report_model", "claude-sonnet-4-6"),
            transport=options.get("report_transport"))
        self.public_guard = PublicApiGuard(db, ring, suite, audit=self.audit)
        self.metrics_token = options.get("metrics_token", "")
        self.settings = options.get("settings")      # 统一策略层实例(C3)
        self.options = options

    def _default_checker_factory(self, options: dict):
        """@brief 生产检测器工厂(ISAPI 真探针在目标环境挂接,GAP-14)"""
        def factory(device, password):
            def probe(host, port, username, pwd, timeout):
                raise OSError("ISAPI 探针未配置(注入 checker_factory)")
            checker = DeviceChecker(
                probe,
                icmp_enabled=options.get("icmp_enabled", True),
                channel_check=options.get("channel_check", True),
                channel_offline_abnormal=options.get(
                    "channel_offline_abnormal", False),
                timeout_seconds=options.get("timeout_seconds", 8))
            return lambda: checker.check(device["host"], device["port"],
                                         device["username"], password)
        return factory

    def guard(self, request: Request, roles: tuple):
        """@brief 会话+角色闸门 @return (user, None) 或 (None, 错误响应)"""
        user, error = require_session(request, self.sso, self.accounts,
                                      cookie_name=self.cookie_name)
        if error:
            return None, error
        denied = require_role(user, roles)
        if denied is not None:
            return None, denied
        return user, None


def error_response(exc: PolicyValidationError) -> JSONResponse:
    """@brief 策略异常 → 人话 JSON"""
    return JSONResponse({"error": str(exc)},
                        status_code=getattr(exc, "http_status", 400))


async def read_json(request: Request) -> dict:
    """@brief 读 JSON 体(空/非法回 {})"""
    raw = await request.body()
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def create_app(db, suite, sso, ring=None, **options) -> FastAPI:
    """
    @brief  装配 nvr 应用(m3 兼容签名:ring 缺省时演示环派生,
            生产必须传平台主密钥环)
    """
    from gd_crypto import MasterKeyRing
    from apps.nvr.web_status import build_status_router
    from apps.nvr.web_ops import build_ops_router
    from apps.nvr.web_settings import build_settings_router

    if ring is None:
        ring = MasterKeyRing.from_env(
            {"MASTER_KEY_HEX": "11" * 32, "MASTER_KEY_ID": "nvr-demo"})
    app = FastAPI(title="港电 NVR 监控", docs_url=None, redoc_url=None)
    ctx = NvrContext(db, ring, suite, sso, options)
    app.state.ctx = ctx
    app.state.accounts = ctx.accounts
    app.include_router(build_sso_router(sso, ctx.accounts,
                                        cookie_name=ctx.cookie_name,
                                        cookie_secure=sso.config.cookie_secure))
    app.include_router(build_status_router(ctx))
    app.include_router(build_ops_router(ctx))
    app.include_router(build_settings_router(ctx))

    # ---- 设备区(auditor 可读,operator 起可写) ------------------------
    @app.get("/devices")
    @app.get("/api/devices")
    def list_devices(request: Request, region: str = None,
                     station: str = None):
        """@brief 设备列表(不返回密码;m3 兼容 /devices)"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return {"devices": ctx.devices.list(region=region, station=station),
                "viewer_role": user["role"]}

    @app.post("/api/devices")
    async def create_device(request: Request):
        """@brief 建设备(推送设备响应含 token,NVR 无该字段)"""
        user, error = ctx.guard(request, ("admin", "operator"))
        if error:
            return error
        payload = await read_json(request)
        try:
            device = ctx.devices.create(payload)
        except PolicyValidationError as exc:
            return error_response(exc)
        ctx.audit.append(user["username"], "settings_changed",
                         {"system": SYSTEM, "device_created": device["id"]},
                         "0.0.0.0")
        return device

    @app.put("/api/devices/{device_id}")
    async def update_device(device_id: int, request: Request):
        """@brief 部分更新(password 传入=轮换)"""
        user, error = ctx.guard(request, ("admin", "operator"))
        if error:
            return error
        payload = await read_json(request)
        try:
            return ctx.devices.update(device_id, payload)
        except PolicyValidationError as exc:
            return error_response(exc)

    @app.delete("/api/devices/{device_id}")
    def delete_device(device_id: int, request: Request):
        """@brief 删除(级联清历史;建议停用代替)"""
        user, error = ctx.guard(request, ("admin",))
        if error:
            return error
        ctx.devices.delete(device_id)
        return {"deleted": device_id}

    @app.post("/api/devices/{device_id}/check")
    def manual_check(device_id: int, request: Request):
        """@brief 手动检测(source=manual 同样入状态机与告警)"""
        user, error = ctx.guard(request, ("admin", "operator"))
        if error:
            return error
        try:
            return ctx.patrol.check_device(device_id, source="manual")
        except PolicyValidationError as exc:
            return error_response(exc)

    @app.post("/api/patrol/run")
    def patrol_run(request: Request):
        """@brief 手动跑一轮(与定时互斥,冲突 409)"""
        user, error = ctx.guard(request, ("admin", "operator"))
        if error:
            return error
        try:
            result = ctx.patrol.run_cycle(source="manual")
        except PolicyValidationError as exc:
            return error_response(exc)
        if result.get("conflict"):
            return JSONResponse({"error": result["error"]}, status_code=409)
        return result

    @app.get("/api/patrol/status")
    def patrol_status(request: Request):
        """@brief 巡检状态"""
        user, error = ctx.guard(request, NVR_ROLES)
        if error:
            return error
        return {"running": ctx.patrol.running,
                "last_cycle": ctx.patrol.last_cycle,
                "next_run_at": ctx.patrol.next_run_at}

    @app.get("/admin/settings")
    def admin_settings(request: Request):
        """@brief 管理设置骨架(m3 兼容;C3 schema 设置页随统一推广)"""
        user, error = ctx.guard(request, ("admin",))
        if error:
            return error
        return {"debounce_modes": list(ALL_MODES),
                "current_mode": ctx.policy.mode}

    @app.get("/healthz")
    def healthz():
        """@brief 健康检查"""
        return {"status": "ok", "system": SYSTEM,
                "sso_enabled": sso.status()["enabled"]}

    return app
