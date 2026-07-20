# -*- coding: utf-8 -*-
"""
@file    main.py
@brief   适配器 FastAPI 薄壳(L01 §2/§4):create_app 工厂、显式
         operation_id、R9 全局错误信封、X-Request-Id 中间件贯通、
         /console 请求时读盘。业务全部在 core(零第三方依赖),
         本层只做装配与协议转换。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from apps.adapter.api import models
from apps.adapter.core import yamlite
from apps.adapter.core.config import Settings, load_settings
from apps.adapter.core.dispatch import (CommandDispatcher, validate_xingluo,
                                        validate_flycart,
                                        validate_cleaning_robot)
from apps.adapter.core.dsl import MappingSpec, translate_events, translate_osd
from apps.adapter.core.errors import BusinessException, FieldError
from apps.adapter.core.features import FeatureRegistry
from apps.adapter.core.forwarder import Forwarder
from apps.adapter.core.ingest import IngestAssembly, now_iso
from apps.adapter.core.poller import Poller
from apps.adapter.core.simulator import seed_simulator
from apps.adapter.core.sink import CompositeSink
from apps.adapter.core.tracing import (build_logger, get_request_id,
                                       set_request_id)
from apps.adapter.core.vendors.flighthub import FlightHubClient
from apps.adapter.core.vendors.siyun import SiyunClient
from apps.adapter.core.vendors.skysys import SkysysClient
from apps.adapter.core.vendors.transport import HttpTransport
from apps.adapter.core.vendors.zhiguang import ZhiguangClient

ADAPTER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(ADAPTER_DIR))
MAPPINGS_DIR = os.path.join(REPO_ROOT, "harness", "mappings")
CONSOLE_PATH = os.path.join(ADAPTER_DIR, "web", "console.html")
SPA_DIST = os.path.join(ADAPTER_DIR, "web", "dist")   # F2 管理 SPA(里程碑 9)


def load_specs(directory: str = MAPPINGS_DIR) -> dict:
    """@brief 加载映射声明(source 为键;R-AD-1:契约工件目录)"""
    specs = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".yaml"):
            continue
        spec = MappingSpec(yamlite.load_file(os.path.join(directory, name)))
        specs[spec.vendor] = spec
    return specs


def _assemble(settings: Settings, transports: dict, clock, sleeper):
    """@brief 装配 core 对象图(传输/时钟可注入,测试零等待)"""
    transports = transports or {}
    default_transport = transports.get("default") or HttpTransport()
    ctx = {"settings": settings, "specs": load_specs()}
    ctx["features"] = FeatureRegistry(
        os.path.join(ADAPTER_DIR, settings.feature_file))
    settings.warnings.extend(ctx["features"].warnings)
    ctx["sink"] = CompositeSink(
        queue_maxlen=settings.sink_queue_maxlen,
        recent_maxlen=settings.recent_events_maxlen,
        raw_maxlen=settings.raw_log_maxlen,
        dedupe_ttl_s=settings.dedupe_ttl_s)
    ctx["zhiguang"] = ZhiguangClient(
        settings, transports.get("zhiguang", default_transport))
    ctx["skysys"] = SkysysClient(
        settings, transports.get("skysys", default_transport))
    ctx["siyun"] = SiyunClient(
        settings, transports.get("siyun", default_transport))
    ctx["flighthub"] = FlightHubClient(
        settings, transports.get("flighthub", default_transport))
    kwargs = {}
    if clock:
        kwargs = {"clock": clock, "sleeper": sleeper}
    ctx["dispatcher"] = CommandDispatcher(settings, **kwargs)
    ctx["ingest"] = IngestAssembly(ctx["sink"], ctx["specs"],
                                   zhiguang=ctx["zhiguang"],
                                   skysys=ctx["skysys"], siyun=ctx["siyun"])
    ctx["poller"] = Poller(tick_s=settings.poller_tick_s)
    ctx["ingest"].register_jobs(ctx["poller"], settings)
    ctx["forwarder"] = Forwarder(
        settings, ctx["sink"],
        transports.get("downstream", default_transport))
    ctx["poller"].add_job("forward_flush", settings.forward_flush_interval_s,
                          ctx["forwarder"].flush,
                          gate=lambda: ctx["forwarder"].enabled)
    seed_simulator(ctx["sink"], settings.simulator_sn_list(), now_iso())
    return ctx


def create_app(settings: Settings = None, transports: dict = None,
               clock=None, sleeper=None) -> FastAPI:
    """@brief 装配适配器应用(uvicorn --factory 入口)"""
    settings = settings or load_settings(dict(os.environ))
    ctx = _assemble(settings, transports, clock, sleeper)
    logger = build_logger()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """@brief 生产生命周期:起停轮询线程(测试不经 lifespan)"""
        ctx["poller"].start()
        logger.info("adapter 启动:轮询器就绪")
        yield
        ctx["poller"].stop()

    app = FastAPI(title="Gd-港电 云云对接多平台适配器", version="2.0.0",
                  lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """@brief X-Request-Id 贯通(响应体/响应头/日志同 id)"""
        rid = set_request_id(request.headers.get("x-request-id", ""))
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(BusinessException)
    async def business_handler(request: Request, exc: BusinessException):
        """@brief R9:业务异常统一转 AdapterResult 信封"""
        logger.info("业务异常:%s %s", exc.code, exc.message)
        body = models.AdapterResult(
            code=exc.http_status, message=exc.message,
            request_id=get_request_id(),
            data=dict(exc.data, error=exc.code))
        return JSONResponse(status_code=exc.http_status,
                            content=body.model_dump())

    _mount_status_routes(app, ctx)
    _mount_command_routes(app, ctx)
    _mount_webhook_routes(app, ctx)
    from apps.rp_common.spa import mount_spa
    mount_spa(app, SPA_DIST)          # include_in_schema=False:openapi 契约零漂移
    return app


def _mount_status_routes(app: FastAPI, ctx: dict):
    """@brief 健康与状态路由(L01 §4 表 01)"""

    @app.get("/healthz", operation_id="healthz",
             response_model=models.HealthzStatus)
    async def healthz():
        """@brief 仅进程存活;接线情况看 runtime"""
        return models.HealthzStatus()

    @app.get("/api/v1/status/features", operation_id="status_features",
             response_model=models.FeaturesStatus)
    async def status_features():
        """@brief feature 清单视图"""
        return models.FeaturesStatus(features=ctx["features"].view())

    @app.get("/api/v1/status/runtime", operation_id="status_runtime",
             response_model=models.RuntimeStatus)
    async def status_runtime():
        """@brief 接线状态 + sink 队列 + poller.jobs + 外发统计 + 原始报文"""
        providers = {name: {"configured": ctx[name].configured}
                     for name in ("zhiguang", "skysys", "siyun", "flighthub")}
        return models.RuntimeStatus(
            providers=providers, sink=ctx["sink"].queue_view(),
            poller_jobs=ctx["poller"].jobs_view(),
            forwarder=ctx["forwarder"].view(),
            commands=ctx["dispatcher"].stats,
            env_warnings=list(ctx["settings"].warnings),
            raw_log=list(ctx["sink"].raw_log))

    @app.get("/api/v1/status/devices", operation_id="status_devices",
             response_model=models.DevicesSnapshot,
             response_model_exclude_none=True)
    async def status_devices():
        """@brief 真实+模拟快照合并(真实优先);空时附 note"""
        return models.DevicesSnapshot(**ctx["sink"].devices_view())

    @app.get("/api/v1/events/recent", operation_id="events_recent",
             response_model=models.RecentEvents)
    async def events_recent(limit: int = Query(default=50),
                            source: str = Query(default=None),
                            event_type: str = Query(default=None)):
        """@brief 旁路环形缓冲查询(钳制 1..容量,newest-first,不消费队列)"""
        return models.RecentEvents(
            **ctx["sink"].recent_view(limit, source, event_type))

    @app.get("/console", operation_id="console", response_class=HTMLResponse,
             include_in_schema=False)
    async def console():
        """@brief 运维控制台(请求时读盘,改前端免重启)"""
        with open(CONSOLE_PATH, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.get("/api/v1/deadletters/export", operation_id="deadletters_export",
             response_model=models.DeadLetterExport)
    async def deadletters_export():
        """@brief 13-R-AD-3:死信队列导出(JSON Lines,一行一事件)"""
        ctx["features"].ensure("forwarder")
        forwarder = ctx["forwarder"]
        return models.DeadLetterExport(
            count=len(forwarder.dead_letters),
            jsonl=forwarder.export_dead_letters())

    @app.post("/api/v1/deadletters/replay", operation_id="deadletters_replay",
              response_model=models.DeadLetterReplayResult)
    async def deadletters_replay(request: models.DeadLetterReplayRequest):
        """@brief 13-R-AD-3:死信重放(已投递的经 DedupeCache 跳过)"""
        ctx["features"].ensure("forwarder")
        outcome = ctx["forwarder"].replay(request.jsonl)
        return models.DeadLetterReplayResult(**outcome)


def _mount_command_routes(app: FastAPI, ctx: dict):
    """@brief 命令下行路由(L01 §4 表 02;条件必填→400)"""
    dispatcher, ingest = ctx["dispatcher"], ctx["ingest"]

    @app.post("/api/v1/commands/xingluo", operation_id="commands_xingluo",
              response_model=models.XingluoCommandResult,
              response_model_exclude_none=True)
    async def commands_xingluo(request: models.XingluoCommandRequest):
        """@brief 星逻无人机命令(takeoff 成功后登记批次续跟)"""
        ctx["features"].ensure("commands_xingluo")
        payload = request.model_dump(exclude_none=True)
        command = validate_xingluo(payload)
        outcome = dispatcher.dispatch(ctx["skysys"], ctx["specs"]["skysys"],
                                      payload)
        if command == "takeoff" and outcome["handle"]:
            ingest.batches.register(outcome["handle"])
        return models.XingluoCommandResult(
            command=command, status=outcome["status"],
            mission_batch=outcome["handle"], uav_id=payload.get("uav_id"),
            vendor_payload=outcome["ack"])

    @app.post("/api/v1/commands/flycart", operation_id="commands_flycart",
              response_model=models.FlyCartCommandResult,
              response_model_exclude_none=True)
    async def commands_flycart(request: models.FlyCartCommandRequest):
        """@brief FlyCart 命令(bid 终态礼貌轮询)"""
        ctx["features"].ensure("commands_flycart")
        payload = request.model_dump(exclude_none=True)
        command = validate_flycart(payload)
        outcome = dispatcher.dispatch(ctx["siyun"], ctx["specs"]["siyun"],
                                      payload)
        task_id = payload.get("task_id") \
            or (outcome["ack"].get("data") or {}).get("task_id")
        return models.FlyCartCommandResult(
            device_sn=payload["device_sn"], command=command,
            status=outcome["status"], task_id=task_id,
            bid=outcome["handle"], vendor_payload=outcome["ack"])

    @app.post("/api/v1/commands/cleaning-robot",
              operation_id="commands_cleaning_robot",
              response_model=models.CleaningRobotCommandResult,
              response_model_exclude_none=True)
    async def commands_cleaning_robot(
            request: models.CleaningRobotCommandRequest):
        """@brief 清洁机器人命令(ack 受理语义)"""
        ctx["features"].ensure("commands_cleaning_robot")
        payload = request.model_dump(exclude_none=True)
        command = validate_cleaning_robot(payload)
        outcome = dispatcher.dispatch(ctx["zhiguang"],
                                      ctx["specs"]["zhiguang"], payload)
        return models.CleaningRobotCommandResult(
            robot_id=payload["robot_id"], command=command,
            status=outcome["status"], vendor_payload=outcome["ack"])


def _mount_webhook_routes(app: FastAPI, ctx: dict):
    """@brief 入站 Webhook 路由(L01 §4 表 03;验签覆盖原始 body 字节)"""
    sink, specs = ctx["sink"], ctx["specs"]

    @app.post("/api/v1/webhooks/zhiguang", operation_id="webhook_zhiguang",
              response_model=models.ZhiguangWebhookAccepted)
    async def webhook_zhiguang(request: Request):
        """@brief 织光推送(strict 失败 401 / log 只记 / off)"""
        ctx["features"].ensure("webhook_zhiguang")
        raw = await request.body()
        signature = request.headers.get("x-zg-signature", "")
        valid = ctx["zhiguang"].verify_webhook(raw, signature)
        try:
            payload = ctx["zhiguang"].parse_webhook(raw)
        except ValueError as exc:
            raise FieldError(str(exc)) from exc
        stamp = now_iso()
        sink.record_raw("zhiguang", payload, stamp, signature_valid=valid)
        events = translate_events(specs["zhiguang"], payload, stamp)
        event_id = events[0].event_id if events else ""
        for event in events:
            sink.emit(event)
        return models.ZhiguangWebhookAccepted(event_id=event_id,
                                              signature_valid=valid)

    @app.post("/api/v1/webhooks/siyun", operation_id="webhook_siyun",
              response_model=models.SiyunWebhookAccepted)
    async def webhook_siyun(request: Request):
        """@brief 司运推送(TD-022 验签;OSD 可选 + 事件分别 emit)"""
        ctx["features"].ensure("webhook_siyun")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise FieldError(f"司运推送体非法 JSON:{exc}") from exc
        event_type = payload.get("event_type")
        sub_type = payload.get("sub_type")
        if not event_type or not sub_type:
            raise FieldError("司运推送缺少 event_type/sub_type")
        headers = {name.lower(): value
                   for name, value in request.headers.items()}
        ctx["siyun"].verify_webhook(headers, event_type, sub_type)
        payload.setdefault("id", headers.get("x-dji-nonce")
                           or get_request_id())
        stamp = now_iso()
        sink.record_raw("siyun", payload, stamp, signature_valid=True)
        osd_updated = False
        if payload.get("deviceSn"):
            sink.emit_osd(translate_osd(specs["siyun"], payload, stamp))
            osd_updated = True
        events = translate_events(specs["siyun"], payload, stamp)
        for event in events:
            sink.emit(event)
        return models.SiyunWebhookAccepted(
            event_id=events[0].event_id if events else "",
            osd_updated=osd_updated)

    @app.post("/api/v1/webhooks/flighthub-sync",
              operation_id="webhook_flighthub_sync",
              response_model=models.FlightHubSyncAccepted)
    async def webhook_flighthub_sync(request: Request):
        """@brief 司空2 Sync(feature=planned→501;启用后通用信封保链路)"""
        ctx["features"].ensure("flighthub_sync")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise FieldError(f"司空2 推送体非法 JSON:{exc}") from exc
        payload.setdefault("id", get_request_id())
        stamp = now_iso()
        sink.record_raw("flighthub", payload, stamp)
        events = translate_events(specs["flighthub"], payload, stamp)
        for event in events:
            sink.emit(event)
        return models.FlightHubSyncAccepted(
            event_id=events[0].event_id if events else "")
