# -*- coding: utf-8 -*-
"""
@file    alarms.py
@brief   告警引擎状态机(L03 §6 契约):
         ok ──offline──▶ pending ──超过 delay_min──▶ active ──ack──▶ acked;
         恢复上线即 clear 本轮(pending/active/acked 皆然),pending 期恢复=
         silent 抖动不算正式告警;再次掉线视为全新告警重走流程;设备删除→清状态,
         active/acked 记 cleared 入历史;历史有界(f3d_alarm_history 环形语义)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import time

STATE_PENDING = "pending"
STATE_ACTIVE = "active"
STATE_ACKED = "acked"
OUTCOME_SILENT = "silent"       # pending 期恢复:抖动,不算正式告警
OUTCOME_CLEARED = "cleared"     # active/acked 后恢复或设备删除


def _iso(epoch: float) -> str:
    """@brief epoch 秒 → ISO 时间串(库内统一 TEXT 时间)"""
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).isoformat()


def _epoch(iso: str) -> float:
    """@brief ISO 时间串 → epoch 秒"""
    return datetime.datetime.fromisoformat(iso).timestamp()


class AlarmEngine:
    """离线告警状态机(持久化在 f3d_alarms/f3d_alarm_history,重启不丢)。"""

    def __init__(self, db, settings):
        """@brief settings=统一策略层实例(delay/历史容量热生效)"""
        self._db = db
        self._settings = settings

    # ---- 参数(逐次读取=热生效,H03 §4) --------------------------------
    def _delay_seconds(self) -> float:
        """@brief 告警延时(分钟→秒,TYPE_FLOAT 允许小数)"""
        return float(self._settings.get("f3d_alarm_delay_minutes")) * 60.0

    def _history_cap(self) -> int:
        """@brief 历史环形容量 HISTORY_CAP"""
        return int(self._settings.get("f3d_alarm_history_cap"))

    # ---- 状态推进 ---------------------------------------------------------
    def _row(self, device_id: str):
        """@brief 读取设备当前告警行 @return (id, state, started_at)|None"""
        rows = self._db.query(
            "SELECT id, state, started_at FROM f3d_alarms WHERE device_id = ?",
            (device_id,))
        return rows[0] if rows else None

    def on_offline(self, device_id: str, now: float = None):
        """@brief 设备掉线:无在途告警则开新一轮 pending(再掉线=全新告警)"""
        now = time.time() if now is None else now
        if self._row(device_id) is None:
            self._db.execute(
                "INSERT INTO f3d_alarms(device_id, state, started_at)"
                " VALUES(?, ?, ?)", (device_id, STATE_PENDING, _iso(now)))

    def on_online(self, device_id: str, now: float = None):
        """@brief 设备恢复:无论 pending/active/acked 一律 clear 本轮入历史"""
        now = time.time() if now is None else now
        row = self._row(device_id)
        if row is None:
            return
        alarm_id, state, started_at = row
        outcome = OUTCOME_SILENT if state == STATE_PENDING else OUTCOME_CLEARED
        self._close(alarm_id, device_id, state, started_at, outcome, now)

    def device_removed(self, device_id: str, now: float = None):
        """@brief 设备删除:清状态;active/acked 记 cleared 入历史"""
        now = time.time() if now is None else now
        row = self._row(device_id)
        if row is None:
            return
        alarm_id, state, started_at = row
        outcome = OUTCOME_SILENT if state == STATE_PENDING else OUTCOME_CLEARED
        self._close(alarm_id, device_id, state, started_at, outcome, now)

    def _close(self, alarm_id: int, device_id: str, state: str,
               started_at: str, outcome: str, now: float):
        """@brief 关闭一轮告警并写历史(含故障总时长),裁剪到容量"""
        self._db.execute("DELETE FROM f3d_alarms WHERE id = ?", (alarm_id,))
        duration = max(0, int(now - _epoch(started_at)))
        self._db.execute(
            "INSERT INTO f3d_alarm_history(device_id, outcome, reached,"
            " started_at, ended_at, duration_seconds) VALUES(?, ?, ?, ?, ?, ?)",
            (device_id, outcome, state, started_at, _iso(now), duration))
        cap = self._history_cap()
        self._db.execute(
            "DELETE FROM f3d_alarm_history WHERE id NOT IN"
            " (SELECT id FROM f3d_alarm_history ORDER BY id DESC LIMIT ?)",
            (cap,))

    def tick(self, now: float = None) -> list:
        """
        @brief  周期推进:pending 满 delay → active(正式告警)
        @return 本次转正的 device_id 列表(供事件流/广播)
        """
        now = time.time() if now is None else now
        threshold = self._delay_seconds()
        promoted = []
        for alarm_id, device_id, started_at in self._db.query(
                "SELECT id, device_id, started_at FROM f3d_alarms"
                " WHERE state = ?", (STATE_PENDING,)):
            if now - _epoch(started_at) >= threshold:
                self._db.execute(
                    "UPDATE f3d_alarms SET state = ?, activated_at = ?"
                    " WHERE id = ?", (STATE_ACTIVE, _iso(now), alarm_id))
                promoted.append(device_id)
        return promoted

    def ack(self, alarm_id: int = None, device_id: str = None,
            ack_all: bool = False, now: float = None) -> int:
        """@brief 消除告警:active → acked(HUD 移出) @return 消除条数"""
        now = time.time() if now is None else now
        clauses, args = ["state = ?"], [STATE_ACTIVE]
        if not ack_all:
            if alarm_id is not None:
                clauses.append("id = ?")
                args.append(alarm_id)
            elif device_id is not None:
                clauses.append("device_id = ?")
                args.append(device_id)
            else:
                return 0
        rows = self._db.query(
            f"SELECT id FROM f3d_alarms WHERE {' AND '.join(clauses)}",
            tuple(args))
        for (row_id,) in rows:
            self._db.execute(
                "UPDATE f3d_alarms SET state = ?, acked_at = ? WHERE id = ?",
                (STATE_ACKED, _iso(now), row_id))
        return len(rows)

    # ---- 查询 -------------------------------------------------------------
    def counts(self) -> dict:
        """@brief 三态计数 {active, pending, acked}"""
        result = {STATE_ACTIVE: 0, STATE_PENDING: 0, STATE_ACKED: 0}
        for state, total in self._db.query(
                "SELECT state, COUNT(*) FROM f3d_alarms GROUP BY state"):
            result[state] = total
        return result

    def active_list(self) -> list:
        """@brief HUD 只展示 active(L03 §6)"""
        return [{"id": row[0], "device_id": row[1], "started_at": row[2],
                 "activated_at": row[3]}
                for row in self._db.query(
                    "SELECT id, device_id, started_at, activated_at"
                    " FROM f3d_alarms WHERE state = ? ORDER BY id",
                    (STATE_ACTIVE,))]

    def recent_history(self, limit: int = 50) -> list:
        """@brief 历史记录(倒序)"""
        return [{"device_id": row[0], "outcome": row[1], "reached": row[2],
                 "started_at": row[3], "ended_at": row[4],
                 "duration_seconds": row[5]}
                for row in self._db.query(
                    "SELECT device_id, outcome, reached, started_at, ended_at,"
                    " duration_seconds FROM f3d_alarm_history"
                    " ORDER BY id DESC LIMIT ?", (limit,))]

    def state_of(self, device_id: str) -> str:
        """@brief 设备当前告警态('' = 无在途告警)"""
        row = self._row(device_id)
        return row[1] if row else ""
