# -*- coding: utf-8 -*-
"""
@file    ingest.py
@brief   南向采集装配(L01 §7):把厂商客户端 + 映射声明 + CompositeSink
         组装成轮询任务(织光机器人 30s/告警 60s/清扫任务 60s、星逻在飞
         批次 5s、司运物模型 15s/任务 60s)。星逻批次续跟:takeoff 登记 →
         状态变化产事件 → 终态摘除,单批失败不阻塞其余批次。
         推送与轮询共用 translate,同 event_id 在 sink 互斥。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from datetime import datetime, timezone

from apps.adapter.core.dsl import get_path, translate_events, translate_osd


def now_iso() -> str:
    """@brief 统一 UTC 时间串"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BatchTracker:
    """星逻批次续跟登记表(dispatch 侧 takeoff 成功后登记)。"""

    def __init__(self):
        """@brief 初始化登记表"""
        self._batches = {}
        self.last_error = ""

    def register(self, batch: str):
        """@brief 登记在飞批次"""
        if batch:
            self._batches[str(batch)] = ""

    def active(self) -> list:
        """@brief 当前跟踪批次"""
        return sorted(self._batches)

    def update(self, batch: str, status: str, terminal: bool) -> bool:
        """@brief 状态推进 @return True=状态发生变化"""
        changed = self._batches.get(batch) != status
        if terminal:
            self._batches.pop(batch, None)
        elif batch in self._batches:
            self._batches[batch] = status
        return changed


class IngestAssembly:
    """采集装配:持有客户端/映射/汇,产出轮询任务函数。"""

    def __init__(self, sink, specs: dict, zhiguang=None, skysys=None,
                 siyun=None):
        """@brief 绑定依赖(specs: source → MappingSpec)"""
        self.sink = sink
        self.specs = specs
        self.zhiguang = zhiguang
        self.skysys = skysys
        self.siyun = siyun
        self.batches = BatchTracker()

    def _emit_osd_rows(self, spec, rows: list, source: str):
        """@brief 一批原始报文 → 快照(逐条隔离)"""
        stamp = now_iso()
        for raw in rows:
            self.sink.record_raw(source, raw, stamp)
            self.sink.emit_osd(translate_osd(spec, raw, stamp))

    def _emit_event_rows(self, spec, rows: list, source: str):
        """@brief 一批原始报文 → 统一事件(同键互斥在 sink)"""
        stamp = now_iso()
        for raw in rows:
            self.sink.record_raw(source, raw, stamp)
            for event in translate_events(spec, raw, stamp):
                self.sink.emit(event)

    def job_zg_robots(self):
        """@brief 织光机器人 OSD 轮询"""
        self._emit_osd_rows(self.specs["zhiguang"],
                            self.zhiguang.fetch_robots(), "zhiguang")

    def job_zg_alarms(self):
        """@brief 织光告警轮询"""
        self._emit_event_rows(self.specs["zhiguang"],
                              self.zhiguang.fetch_alarms(), "zhiguang")

    def job_zg_tasks(self):
        """@brief 织光清扫任务轮询"""
        self._emit_event_rows(self.specs["zhiguang"],
                              self.zhiguang.fetch_tasks(), "zhiguang")

    def job_sky_batches(self):
        """@brief 星逻在飞批次轮询 + 登记批次续跟(单批失败不阻塞)"""
        spec = self.specs["skysys"]
        rows = self.skysys.fetch_active_batches()
        self._emit_event_rows(spec, rows, "skysys")
        reply = spec.reply or {}
        terminal = reply.get("terminal") or {}
        succeeded = [str(x) for x in (terminal.get("succeeded") or [])]
        failed = [str(x) for x in (terminal.get("failed") or [])]
        for batch in self.batches.active():
            try:
                doc = self.skysys.query(terminal.get("query", "batch_status"),
                                        batch)
                status = str(get_path(doc, terminal.get("path", "status")))
                is_terminal = status in succeeded or status in failed
                if self.batches.update(batch, status, is_terminal):
                    self._emit_event_rows(spec, [{
                        "missionBatch": batch, "status": status,
                        "terminal": is_terminal}], "skysys")
            except Exception as exc:      # 单批隔离,下一轮重试
                self.batches.last_error = f"批次 {batch}:{exc}"
                continue

    def job_siyun_props(self):
        """@brief 司运物模型轮询"""
        self._emit_osd_rows(self.specs["siyun"],
                            self.siyun.fetch_properties(), "siyun")

    def job_siyun_tasks(self):
        """@brief 司运任务轮询(code!=0 或 status=6 已带 warn 提示)"""
        self._emit_event_rows(self.specs["siyun"],
                              self.siyun.fetch_tasks(), "siyun")

    def register_jobs(self, poller, settings):
        """@brief 全部任务按 L01 §7 间隔登记(客户端未配置自动门控)"""
        table = [
            ("zg_robots", settings.ingest_zg_robots_interval_s,
             self.job_zg_robots, self.zhiguang),
            ("zg_alarms", settings.ingest_zg_alarms_interval_s,
             self.job_zg_alarms, self.zhiguang),
            ("zg_tasks", settings.ingest_zg_tasks_interval_s,
             self.job_zg_tasks, self.zhiguang),
            ("sky_batches", settings.ingest_sky_batches_interval_s,
             self.job_sky_batches, self.skysys),
            ("siyun_props", settings.ingest_siyun_props_interval_s,
             self.job_siyun_props, self.siyun),
            ("siyun_tasks", settings.ingest_siyun_tasks_interval_s,
             self.job_siyun_tasks, self.siyun),
        ]
        for name, interval, fn, client in table:
            if client is None:
                continue
            poller.add_job(name, interval, fn,
                           gate=lambda cli=client: cli.configured)
