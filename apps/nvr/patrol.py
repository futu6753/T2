# -*- coding: utf-8 -*-
"""
@file    patrol.py
@brief   巡检服务(L04 §7):同一时刻至多一轮(互斥),并发上限线程池,
         单台解密失败只影响该台(异常+原因现于面板),主密钥缺失跳过整轮
         报错;每轮返回 total/checked/by_status/changes/duration_ms/errors;
         定时调度由外层驱动(超时顺延打日志);检测后驱动告警引擎与
         通道台账;保留期清理随轮执行。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from gd_common.errors import PolicyValidationError
from gd_common.jsonlog import get_logger
from apps.nvr.checker import STATUS_ABNORMAL

_log = get_logger("nvr.patrol")


class PatrolService:
    """定时/手动巡检执行器。"""

    def __init__(self, devices, checker_factory, alert_engine,
                 channels_service=None, concurrency: int = 10,
                 retention_days: int = 90, master_key_ready=lambda: True):
        """
        @brief  装配依赖
        @param  checker_factory (device, password) → DeviceChecker.check 可调
                (探针注入点;测试传 fake)
        """
        self._devices = devices
        self._checker_factory = checker_factory
        self._alerts = alert_engine
        self._channels = channels_service
        self._concurrency = max(int(concurrency), 1)
        self._retention_days = retention_days
        self._master_key_ready = master_key_ready
        self._lock = threading.Lock()
        self.last_cycle = None
        self.next_run_at = None

    @property
    def running(self) -> bool:
        """@brief 是否有巡检轮进行中"""
        return self._lock.locked()

    def run_cycle(self, source: str = "patrol") -> dict:
        """
        @brief  执行一轮巡检(与其他轮互斥;冲突时抛 409 语义异常)
        @return {total, checked, by_status, changes, duration_ms, errors}
        """
        if not self._master_key_ready():
            _log.error("主密钥缺失,本轮巡检整轮跳过")
            raise PolicyValidationError("主密钥缺失,巡检无法执行")
        if not self._lock.acquire(blocking=False):
            error = PolicyValidationError("已有巡检进行中")
            error.http_status = 409
            return {"error": str(error), "conflict": True}
        started = time.monotonic()
        by_status, errors, changes = {}, {}, 0
        try:
            targets = [device for device in self._devices.list(enabled=True)
                       if device["kind"] == "nvr"]
            with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
                outcomes = list(pool.map(
                    lambda device: self._check_one(device, source, errors),
                    targets))
            for outcome in outcomes:
                if outcome is None:
                    continue
                by_status[outcome["status"]] = \
                    by_status.get(outcome["status"], 0) + 1
                if outcome["changed"]:
                    changes += 1
            if self._retention_days > 0:
                self._devices.prune(self._retention_days)
            result = {"total": len(targets),
                      "checked": sum(by_status.values()),
                      "by_status": by_status, "changes": changes,
                      "duration_ms": int((time.monotonic() - started) * 1000),
                      "errors": errors}
            self.last_cycle = {"at": datetime.now(timezone.utc).isoformat(),
                               **result}
            return result
        finally:
            self._lock.release()

    def _check_one(self, device: dict, source: str, errors: dict):
        """@brief 单台检测(解密失败只影响该台=异常+原因现于面板)"""
        try:
            password = self._devices.open_password(device["id"])
        except Exception as exc:
            outcome = self._devices.record_check(
                device["id"], STATUS_ABNORMAL,
                f"设备密码解密失败: {type(exc).__name__}", 0, source)
            errors[device["name"]] = "password_decrypt_failed"
            self._alerts.on_check(device, outcome)
            return {"status": STATUS_ABNORMAL, "changed": outcome["changed"]}
        try:
            check = self._checker_factory(device, password)()
        except Exception as exc:             # 检测器故障隔离
            errors[device["name"]] = str(exc)[:120]
            return None
        finally:
            del password                     # 凭据即用即释
        outcome = self._devices.record_check(
            device["id"], check["status"], check["detail"],
            check["latency_ms"], source)
        if self._channels is not None:
            self._channels.sync_from_check(device["id"], check)
        self._alerts.on_check(device, outcome,
                              offline_channels=check.get("offline_channels"))
        return {"status": check["status"], "changed": outcome["changed"]}

    def check_device(self, device_id: int, source: str = "manual") -> dict:
        """@brief 手动检测单台(source=manual 同样入状态机与告警,契约)"""
        device = self._devices.get(device_id)
        if device is None:
            raise PolicyValidationError("设备不存在")
        result = self._check_one(device, source, {})
        state = self._devices.state_of(device_id)
        return {"device_id": device_id, "status": state["status"],
                "detail": state["last_detail"],
                "checked": result is not None}
