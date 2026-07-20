# -*- coding: utf-8 -*-
"""
@file    debounce.py
@brief   13-R-NVR-1 去抖策略族(五模式 choice):
         consecutive_failures(遗留:连续失败 N 次)、
         offline_duration(遗留:持续故障 T 秒,窗口起点自时间线推导,
         子状态切换与进程重启都不重置)、
         ewma(指数加权故障分,平滑瞬断)、
         hysteresis(滞回:触发阈严/恢复阈松,防边界抖动)、
         adaptive(自适应:按设备历史抖动率放大基础阈值)。
         统一接口 should_fire(state_snapshot)→bool;恢复判定统一
         should_resolve(status)。回放评测脚本见 benchmarks/debounce_replay.py
         (误报-延迟 Pareto,13-R-NVR-1)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_common.errors import PolicyValidationError

MODE_CONSECUTIVE = "consecutive_failures"
MODE_DURATION = "offline_duration"
MODE_EWMA = "ewma"
MODE_HYSTERESIS = "hysteresis"
MODE_ADAPTIVE = "adaptive"
ALL_MODES = (MODE_CONSECUTIVE, MODE_DURATION, MODE_EWMA,
             MODE_HYSTERESIS, MODE_ADAPTIVE)

EWMA_ALPHA = 0.4                     # 故障分平滑系数
EWMA_FIRE_THRESHOLD = 0.75           # 触发阈
HYSTERESIS_FIRE = 3                  # 滞回:触发需连续失败次数(严)
HYSTERESIS_RESOLVE_OK = 2            # 滞回:恢复需连续成功次数(松→防抖)
ADAPTIVE_BASE = 3                    # 自适应基础阈
ADAPTIVE_MAX = 8                     # 自适应上限


class DebouncePolicy:
    """去抖判定(状态快照驱动,无内部可变态=进程重启不重置语义天然满足)。

    快照字段:status、consecutive_fails、offline_seconds、ewma、
    consecutive_ok(滞回恢复用)、flap_rate(自适应:近期跃迁率 0–1)。
    """

    def __init__(self, mode: str, consecutive_failures: int = 3,
                 offline_duration_seconds: int = 300):
        """@brief 装配模式与遗留双参(H03 兼容默认)"""
        if mode not in ALL_MODES:
            raise PolicyValidationError(f"未知去抖模式: {mode}")
        self.mode = mode
        self._threshold = max(int(consecutive_failures), 1)
        self._duration = max(int(offline_duration_seconds), 1)

    def should_fire(self, snapshot: dict) -> bool:
        """@brief 是否触发告警(仅对非在线状态调用)"""
        if snapshot.get("status") == "online":
            return False
        handler = {
            MODE_CONSECUTIVE: self._fire_consecutive,
            MODE_DURATION: self._fire_duration,
            MODE_EWMA: self._fire_ewma,
            MODE_HYSTERESIS: self._fire_hysteresis,
            MODE_ADAPTIVE: self._fire_adaptive,
        }[self.mode]
        return handler(snapshot)

    def should_resolve(self, snapshot: dict) -> bool:
        """@brief 是否解除(恢复立即解除;滞回模式要求连续成功)"""
        if snapshot.get("status") != "online":
            return False
        if self.mode == MODE_HYSTERESIS:
            return snapshot.get("consecutive_ok", 1) >= HYSTERESIS_RESOLVE_OK
        return True

    # ---- 各模式 ---------------------------------------------------------
    def _fire_consecutive(self, snapshot: dict) -> bool:
        """@brief 遗留模式一:连续失败 ≥N"""
        return snapshot.get("consecutive_fails", 0) >= self._threshold

    def _fire_duration(self, snapshot: dict) -> bool:
        """@brief 遗留模式二:非在线持续 ≥T 秒(起点自时间线,重启不重置)"""
        return snapshot.get("offline_seconds", 0) >= self._duration

    def _fire_ewma(self, snapshot: dict) -> bool:
        """@brief EWMA 故障分 ≥阈(瞬断被平滑,持续故障快速爬升)"""
        return snapshot.get("ewma", 0.0) >= EWMA_FIRE_THRESHOLD

    def _fire_hysteresis(self, snapshot: dict) -> bool:
        """@brief 滞回:触发阈独立于恢复阈"""
        return snapshot.get("consecutive_fails", 0) >= HYSTERESIS_FIRE

    def _fire_adaptive(self, snapshot: dict) -> bool:
        """@brief 自适应:阈值 = base × (1 + 抖动率放大),抖动设备更迟钝"""
        flap_rate = min(max(snapshot.get("flap_rate", 0.0), 0.0), 1.0)
        threshold = min(int(ADAPTIVE_BASE * (1 + 2 * flap_rate)),
                        ADAPTIVE_MAX)
        return snapshot.get("consecutive_fails", 0) >= threshold


def next_ewma(previous: float, is_failure: bool) -> float:
    """@brief EWMA 故障分递推(1=故障样本,0=在线样本)"""
    sample = 1.0 if is_failure else 0.0
    return EWMA_ALPHA * sample + (1 - EWMA_ALPHA) * previous


def flap_rate_from_timeline(db, device_id: int, window: int = 20) -> float:
    """@brief 自适应输入:近 window 条时间线中的状态跃迁密度(0–1)"""
    rows = db.query(
        "SELECT COUNT(*) FROM (SELECT id FROM nvr_timeline"
        " WHERE device_id = ? AND event_type = 'status_change'"
        " ORDER BY id DESC LIMIT ?)", (device_id, window))
    return min(rows[0][0] / float(window), 1.0)
