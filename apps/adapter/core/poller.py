# -*- coding: utf-8 -*-
"""
@file    poller.py
@brief   南向单线程轮询器(L01 §7):tick 0.5s;每任务独立间隔与统计
         (间隔/下次/成功/失败/最近错误);任务异常记 stats 不杀循环;
         客户端未配置(BASE_URL 空或 example.com)自动门控不发请求。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import threading
import time


class PollJob:
    """单个轮询任务(fn 无参调用;gate 返回 False 时跳过并标记 gated)。"""

    def __init__(self, name: str, interval_s: float, fn, gate=None):
        """@brief 登记任务"""
        self.name = name
        self.interval_s = float(interval_s)
        self.fn = fn
        self.gate = gate or (lambda: True)
        self.next_run = 0.0
        self.success = 0
        self.fail = 0
        self.last_error = ""
        self.gated = False

    def view(self) -> dict:
        """@brief runtime 展示(轮询任务/间隔/下次/成功/失败/最近错误)"""
        return {"name": self.name, "interval_s": self.interval_s,
                "next_run_in_s": max(0.0, round(self.next_run, 1)),
                "success": self.success, "fail": self.fail,
                "last_error": self.last_error, "gated": self.gated}


class Poller:
    """单线程轮询器(时钟可注入;测试直接驱动 run_pending)。"""

    def __init__(self, tick_s: float = 0.5, clock=time.monotonic):
        """@brief 初始化任务表"""
        self.tick_s = tick_s
        self._clock = clock
        self.jobs = []
        self._stop = threading.Event()
        self._thread = None

    def add_job(self, name: str, interval_s: float, fn, gate=None):
        """@brief 登记任务(立即到期,首轮 tick 即执行)"""
        self.jobs.append(PollJob(name, interval_s, fn, gate))

    def run_pending(self, now: float = None):
        """@brief 执行所有到期任务(异常入 last_error,不杀循环)"""
        now = self._clock() if now is None else now
        for job in self.jobs:
            if now < job.next_run:
                continue
            job.next_run = now + job.interval_s
            if not job.gate():
                job.gated = True
                continue
            job.gated = False
            try:
                job.fn()
                job.success += 1
                job.last_error = ""
            except Exception as exc:      # 单任务隔离(H06-E7)
                job.fail += 1
                job.last_error = f"{type(exc).__name__}: {exc}"

    def jobs_view(self) -> list:
        """@brief runtime 的 poller.jobs.* 数据"""
        now = self._clock()
        rows = []
        for job in self.jobs:
            row = job.view()
            row["next_run_in_s"] = max(0.0, round(job.next_run - now, 1))
            rows.append(row)
        return rows

    def start(self):
        """@brief 后台线程启动(生产形态;测试不调用)"""
        if self._thread:
            return
        self._stop.clear()

        def _loop():
            """@brief tick 循环"""
            while not self._stop.is_set():
                self.run_pending()
                self._stop.wait(self.tick_s)

        self._thread = threading.Thread(target=_loop, name="adapter-poller",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        """@brief 停止后台线程"""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
