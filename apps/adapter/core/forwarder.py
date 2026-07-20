# -*- coding: utf-8 -*-
"""
@file    forwarder.py
@brief   下游 Webhook 外发(L01 §7)与 13-R-AD-3 死信重放:
         DOWNSTREAM_URL 非空启用;批量 ≤50 条/批、2s flush;失败指数退避
         base*2^(n-1),超 5 次入有界死信并放行下一批;DOWNSTREAM_SECRET
         非空时出站签名 X-Adapter-Signature = HmacSHA256(secret,
         timestamp+nonce+SHA256hex(canonical)),canonical = 紧凑分隔符 +
         sort_keys + ensure_ascii=False 的 JSON;sign_downstream() 供对端
         复用验签。已投递事件登记 DedupeCache——重放后下游仍只见一次。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac
import json
import secrets
import time
from collections import deque

from apps.adapter.core.model import UnifiedEvent
from apps.adapter.core.sink import DedupeCache
from apps.adapter.core.vendors.transport import TransportTimeout


def canonical_json(payload) -> str:
    """@brief 规范化 JSON(紧凑分隔符 + sort_keys + ensure_ascii=False)"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def sign_downstream(secret: str, canonical: str, timestamp: str,
                    nonce: str) -> str:
    """
    @brief  出站签名(对端复用本函数验签):
            HmacSHA256(secret, timestamp + nonce + SHA256hex(canonical))
    """
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    message = f"{timestamp}{nonce}{digest}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message,
                    hashlib.sha256).hexdigest()


class Forwarder:
    """外发器(传输/时钟/睡眠可注入)。"""

    def __init__(self, settings, sink, transport, clock=time.monotonic,
                 sleeper=time.sleep):
        """@brief 绑定依赖与统计"""
        self.settings = settings
        self.sink = sink
        self.transport = transport
        self._clock = clock
        self._sleep = sleeper
        self._last_flush = 0.0
        self.dead_letters = deque(maxlen=settings.dead_letter_maxlen)
        self.delivered = DedupeCache(ttl_s=settings.dedupe_ttl_s, clock=clock)
        self.stats = {"batches_sent": 0, "events_sent": 0, "retries": 0,
                      "dead_lettered": 0, "replayed": 0,
                      "replay_skipped": 0}
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        """@brief DOWNSTREAM_URL 非空启用"""
        return bool(self.settings.downstream_url)

    def _headers(self, canonical: str) -> dict:
        """@brief 出站请求头(含可选签名三件套)"""
        headers = {"Content-Type": "application/json"}
        if self.settings.downstream_secret:
            timestamp = str(int(time.time()))
            nonce = secrets.token_hex(8)
            headers["X-Adapter-Timestamp"] = timestamp
            headers["X-Adapter-Nonce"] = nonce
            headers["X-Adapter-Signature"] = sign_downstream(
                self.settings.downstream_secret, canonical, timestamp, nonce)
        return headers

    def _post_batch(self, events: list) -> bool:
        """@brief 单批投递(指数退避;超限入死信并放行)@return 是否成功"""
        payload = {"events": [event.to_dict() for event in events]}
        canonical = canonical_json(payload)
        body = canonical.encode("utf-8")
        for attempt in range(1, self.settings.forward_max_retries + 1):
            try:
                resp = self.transport("POST", self.settings.downstream_url,
                                      headers=self._headers(canonical),
                                      body=body)
                if resp.status < 400:
                    self.stats["batches_sent"] += 1
                    self.stats["events_sent"] += len(events)
                    for event in events:
                        self.delivered.seen(event.event_id)
                    return True
                self.last_error = f"下游 HTTP {resp.status}"
            except TransportTimeout as exc:
                self.last_error = f"下游超时:{exc}"
            except Exception as exc:      # 外发失败只退避,不杀循环
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.stats["retries"] += 1
            if attempt < self.settings.forward_max_retries:
                backoff = self.settings.forward_backoff_base_s \
                    * (2 ** (attempt - 1))
                self._sleep(backoff)
        for event in events:
            self.dead_letters.append(event.to_dict())
            self.stats["dead_lettered"] += 1
        return False

    def maybe_flush(self, now: float = None):
        """@brief 到点或满批即冲(poller 每 tick 调用)"""
        if not self.enabled:
            return
        now = self._clock() if now is None else now
        pending = len(self.sink.outbound)
        due = now - self._last_flush >= self.settings.forward_flush_interval_s
        if pending >= self.settings.forward_batch_max or (pending and due):
            self.flush()
            self._last_flush = now

    def flush(self):
        """@brief 立即外发全部待投(按 ≤batch_max 分批;失败批入死信放行)"""
        if not self.enabled:
            return
        while self.sink.outbound:
            batch = self.sink.drain(self.settings.forward_batch_max)
            if not batch:
                break
            self._post_batch(batch)

    # ---- 13-R-AD-3 死信导出与重放 ----

    def export_dead_letters(self) -> str:
        """@brief 死信导出(JSON Lines,一行一事件)"""
        return "\n".join(canonical_json(item) for item in self.dead_letters)

    def replay(self, exported_text: str = None) -> dict:
        """
        @brief  死信重放:未投递过的重新入队(DedupeCache 协同——
                已投递的跳过,保证下游仍只见一次);默认重放当前死信队列
        @return {enqueued, skipped}
        """
        if exported_text is None:
            rows = list(self.dead_letters)
            self.dead_letters.clear()
        else:
            rows = [json.loads(line) for line in exported_text.splitlines()
                    if line.strip()]
        enqueued = skipped = 0
        for row in rows:
            if self.delivered.peek(row.get("event_id", "")):
                skipped += 1
                self.stats["replay_skipped"] += 1
                continue
            self.sink.outbound.append(UnifiedEvent(**row))
            enqueued += 1
            self.stats["replayed"] += 1
        return {"enqueued": enqueued, "skipped": skipped}

    def view(self) -> dict:
        """@brief runtime 展示(外发统计)"""
        return {"enabled": self.enabled, "last_error": self.last_error,
                "dead_letters": len(self.dead_letters), **self.stats}
