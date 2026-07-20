# -*- coding: utf-8 -*-
"""
@file    features.py
@brief   feature 开关(L01 §2):feature_list.json(id/title/priority/status)
         运行时门控;关闭/规划中的 feature 被调用 → FeatureDisabledError → 501。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os

from apps.adapter.core.errors import FeatureDisabledError

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_PLANNED = "planned"

DEFAULT_FEATURES = [
    {"id": "ingest_zhiguang", "title": "织光南向轮询采集",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "ingest_skysys", "title": "星逻在飞批次轮询",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "ingest_siyun", "title": "司运物模型/任务轮询",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "webhook_zhiguang", "title": "织光入站推送",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "webhook_siyun", "title": "司运入站推送",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "commands_xingluo", "title": "星逻无人机命令下行",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "commands_flycart", "title": "FlyCart 命令下行",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "commands_cleaning_robot", "title": "清洁机器人命令下行",
     "priority": "P0", "status": STATUS_ENABLED},
    {"id": "forwarder", "title": "下游 Webhook 外发",
     "priority": "P1", "status": STATUS_ENABLED},
    {"id": "flighthub_sync", "title": "司空2 Sync 推送(规划中)",
     "priority": "P2", "status": STATUS_PLANNED},
]


class FeatureRegistry:
    """feature 清单:文件存在则读文件,否则用内置默认(交付包内置同构文件)。"""

    def __init__(self, path: str = ""):
        """@brief 加载 feature 清单(文件缺失/损坏回退默认并留告警)"""
        self.warnings = []
        self.features = [dict(item) for item in DEFAULT_FEATURES]
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, list) and loaded:
                    self.features = loaded
            except (OSError, ValueError) as exc:
                self.warnings.append(f"feature 文件 {path} 不可用:{exc},"
                                     f"回退内置默认清单")

    def view(self) -> list:
        """@brief 清单视图(GET /status/features)"""
        return [dict(item) for item in self.features]

    def status_of(self, feature_id: str) -> str:
        """@brief 查询单个 feature 状态(未知视为 disabled)"""
        for item in self.features:
            if item.get("id") == feature_id:
                return item.get("status", STATUS_DISABLED)
        return STATUS_DISABLED

    def ensure(self, feature_id: str):
        """@brief 门控:非 enabled → FeatureDisabledError(501)"""
        status = self.status_of(feature_id)
        if status != STATUS_ENABLED:
            hint = ("规划中(GAP-24):启用后走通用信封链路"
                    if status == STATUS_PLANNED else "已关闭")
            raise FeatureDisabledError(
                f"feature {feature_id} 未启用({hint})",
                data={"feature": feature_id, "status": status})
