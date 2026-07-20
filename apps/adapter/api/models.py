# -*- coding: utf-8 -*-
"""
@file    models.py
@brief   北向契约模型(L01 §2):pydantic 模型类名与接口文档
         components.schemas 完全一致,export 的 openapi.json 可与冻结契约
         逐一 diff。条件必填不在模型层(全 Optional),由 dispatch 校验
         →400(422 保持框架默认,仅覆盖体裁级非法)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from typing import Optional

from pydantic import BaseModel, Field


class HealthzStatus(BaseModel):
    """进程存活(仅存活,不含接线情况)。"""

    status: str = "ok"


class FeatureItem(BaseModel):
    """feature 清单条目。"""

    id: str
    title: str = ""
    priority: str = ""
    status: str = ""


class FeaturesStatus(BaseModel):
    """GET /api/v1/status/features 响应。"""

    features: list[FeatureItem]


class UnifiedOsd(BaseModel):
    """统一遥测快照(北向契约,与 core.model.UnifiedOsd 字段一致)。"""

    sn: str
    source: str
    device_kind: str = "unknown"
    online: bool = False
    updated_at: str = ""
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    altitude: Optional[float] = None
    battery_percent: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    mode_code: Optional[str] = None
    extra: dict = Field(default_factory=dict)


class DevicesSnapshot(BaseModel):
    """GET /api/v1/status/devices 响应(真实+模拟合并,真实优先)。"""

    devices: list[UnifiedOsd]
    real_count: int
    simulated_count: int
    note: Optional[str] = None


class UnifiedEventView(BaseModel):
    """统一事件视图。"""

    event_id: str
    source: str
    event_type: str
    severity: str = "info"
    ts: str = ""
    sn: str = ""
    data: dict = Field(default_factory=dict)


class RecentEvents(BaseModel):
    """GET /api/v1/events/recent 响应(newest-first)。"""

    count: int
    events: list[UnifiedEventView]


class RuntimeStatus(BaseModel):
    """GET /api/v1/status/runtime 响应(接线/队列/轮询/外发/原始报文)。"""

    providers: dict
    sink: dict
    poller_jobs: list
    forwarder: dict
    commands: dict
    env_warnings: list[str] = Field(default_factory=list)
    raw_log: list = Field(default_factory=list)


class AdapterResult(BaseModel):
    """统一错误信封(R9:业务异常全局转此结构)。"""

    code: int
    message: str
    request_id: str = ""
    data: dict = Field(default_factory=dict)


class DeadLetterExport(BaseModel):
    """GET /api/v1/deadletters/export 响应(13-R-AD-3:JSON Lines 导出)。"""

    count: int
    jsonl: str = ""


class DeadLetterReplayRequest(BaseModel):
    """POST /api/v1/deadletters/replay 请求(jsonl 缺省=重放当前死信队列)。"""

    jsonl: Optional[str] = None


class DeadLetterReplayResult(BaseModel):
    """死信重放结果(DedupeCache 协同:已投递的跳过,下游仍只见一次)。"""

    enqueued: int
    skipped: int


class XingluoCommandRequest(BaseModel):
    """星逻命令请求(条件必填由 dispatch 校验→400)。"""

    command: Optional[str] = None
    site_id: Optional[str] = None
    mission_id: Optional[str] = None
    uav_id: Optional[str] = None
    camera_lens: Optional[str] = None
    idempotency_key: Optional[str] = None


class XingluoCommandResult(BaseModel):
    """星逻命令结果(succeeded|accepted)。"""

    command: str
    status: str
    mission_batch: Optional[str] = None
    uav_id: Optional[str] = None
    vendor_payload: dict = Field(default_factory=dict)


class FlyCartCommandRequest(BaseModel):
    """FlyCart 命令请求(device_sn 必填由 dispatch 校验)。"""

    device_sn: Optional[str] = None
    command: Optional[str] = None
    task: Optional[dict] = None
    task_id: Optional[str] = None
    status: Optional[str] = None
    cmd: Optional[dict] = None
    idempotency_key: Optional[str] = None


class FlyCartCommandResult(BaseModel):
    """FlyCart 命令结果。"""

    device_sn: str
    command: str
    status: str
    task_id: Optional[str] = None
    bid: Optional[str] = None
    vendor_payload: dict = Field(default_factory=dict)


class CleaningRobotCommandRequest(BaseModel):
    """清洁机器人命令请求。"""

    command: Optional[str] = None
    robot_id: Optional[str] = None
    status: Optional[str] = None
    scheduling_method: Optional[str] = None
    scheduled_cleaning_at: Optional[str] = None
    idempotency_key: Optional[str] = None


class CleaningRobotCommandResult(BaseModel):
    """清洁机器人命令结果。"""

    robot_id: str
    command: str
    status: str
    vendor_payload: dict = Field(default_factory=dict)


class ZhiguangWebhookAccepted(BaseModel):
    """织光推送受理回执。"""

    accepted: int = 1
    event_id: str
    signature_valid: bool


class SiyunWebhookAccepted(BaseModel):
    """司运推送受理回执。"""

    accepted: int = 1
    event_id: str
    osd_updated: bool = False


class FlightHubSyncAccepted(BaseModel):
    """司空2 Sync 推送受理回执(feature 启用后)。"""

    accepted: int = 1
    event_id: str
