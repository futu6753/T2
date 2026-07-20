# -*- coding: utf-8 -*-
"""
@file    sink.py
@brief   CompositeSink(L01 §3/§7):外发队列 + 遥测快照(真实优先合并)+
         最近事件旁路环形缓冲(newest-first、过滤、钳制)+ 原始报文缓冲 +
         DedupeCache(TTL+容量双限)。事件 event_id 用源侧稳定键,
         轮询与推送同键互斥——同一事实下游只见一次。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time
from collections import OrderedDict, deque

from apps.adapter.core.model import UnifiedEvent, UnifiedOsd, merge_snapshots


class DedupeCache:
    """TTL + 容量双限去重缓存(容量满淘汰最旧)。"""

    def __init__(self, ttl_s: float = 3600.0, capacity: int = 10000,
                 clock=time.monotonic):
        """@brief 初始化双限参数(clock 可注入,测试用假时钟)"""
        self.ttl_s = ttl_s
        self.capacity = capacity
        self._clock = clock
        self._seen = OrderedDict()

    def _evict(self, now: float):
        """@brief 过期与超容淘汰"""
        while self._seen:
            key, stamp = next(iter(self._seen.items()))
            if now - stamp > self.ttl_s:
                self._seen.popitem(last=False)
            else:
                break
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

    def seen(self, key: str) -> bool:
        """@brief 查询并登记:首次 False,重复(未过期)True"""
        now = self._clock()
        self._evict(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        self._evict(now)
        return False

    def peek(self, key: str) -> bool:
        """@brief 只查不登记"""
        now = self._clock()
        self._evict(now)
        return key in self._seen

    def __len__(self):
        """@brief 当前登记数"""
        return len(self._seen)


class CompositeSink:
    """队列 + 快照复合汇(所有北向读接口的唯一数据源)。"""

    def __init__(self, queue_maxlen: int = 1000, recent_maxlen: int = 500,
                 raw_maxlen: int = 200, dedupe_ttl_s: float = 3600.0,
                 clock=time.monotonic):
        """@brief 装配四类缓冲与去重缓存"""
        self.outbound = deque(maxlen=queue_maxlen)
        self.recent = deque(maxlen=recent_maxlen)
        self.raw_log = deque(maxlen=raw_maxlen)
        self.dedupe = DedupeCache(ttl_s=dedupe_ttl_s, clock=clock)
        self._real = {}
        self._simulated = {}
        self.stats = {"events_in": 0, "events_deduped": 0, "osd_in": 0,
                      "queue_dropped": 0}

    def emit(self, event: UnifiedEvent) -> bool:
        """
        @brief  提交统一事件:同 event_id(TTL 内)互斥丢弃;
                入外发队列 + 最近事件环形缓冲
        @return True=接收,False=去重丢弃
        """
        self.stats["events_in"] += 1
        if self.dedupe.seen(event.event_id):
            self.stats["events_deduped"] += 1
            return False
        if len(self.outbound) == self.outbound.maxlen:
            self.stats["queue_dropped"] += 1
        self.outbound.append(event)
        self.recent.append(event)
        return True

    def emit_osd(self, osd: UnifiedOsd):
        """@brief 提交遥测快照(模拟器来源入模拟槽,其余入真实槽)"""
        self.stats["osd_in"] += 1
        slot = self._simulated if osd.source == "simulator" else self._real
        slot[osd.sn] = osd

    def record_raw(self, source: str, payload, ts: str,
                   signature_valid=None):
        """@brief 原始报文缓冲(L01 §4 runtime 展示;含验签结论)"""
        self.raw_log.append({"source": source, "ts": ts,
                             "signature_valid": signature_valid,
                             "payload": payload})

    def devices_view(self) -> dict:
        """@brief GET /status/devices 数据(真实优先合并 + 双计数 + 空提示)"""
        devices = merge_snapshots(self._real, self._simulated)
        view = {"devices": devices, "real_count": len(self._real),
                "simulated_count": len(self._simulated)}
        if not devices:
            view["note"] = "暂无遥测:上游未接通且模拟器未启用"
        return view

    def recent_view(self, limit: int, source: str = None,
                    event_type: str = None) -> dict:
        """
        @brief  GET /events/recent 数据:limit 服务端钳制 1..容量,
                source/event_type 精确过滤,newest-first,不消费外发队列
        """
        capacity = self.recent.maxlen or 1
        limit = max(1, min(int(limit), capacity))
        rows = []
        for event in reversed(self.recent):
            if source and event.source != source:
                continue
            if event_type and event.event_type != event_type:
                continue
            rows.append(event.to_dict())
            if len(rows) >= limit:
                break
        return {"count": len(rows), "events": rows}

    def drain(self, max_items: int) -> list:
        """@brief 外发消费:批量弹出(Forwarder 专用)"""
        batch = []
        while self.outbound and len(batch) < max_items:
            batch.append(self.outbound.popleft())
        return batch

    def queue_view(self) -> dict:
        """@brief runtime 展示的队列/统计"""
        return {"outbound_pending": len(self.outbound),
                "recent_buffered": len(self.recent),
                "raw_buffered": len(self.raw_log),
                "dedupe_entries": len(self.dedupe), **self.stats}
