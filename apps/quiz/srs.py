# -*- coding: utf-8 -*-
"""
@file    srs.py
@brief   13-R-QZ-1 间隔重复调度(SM-2 变体):ease 以 ×100 整数存储
         (H07 L1-06 禁浮点相等);按题型/底色分层排期——难题型(风险问答/
         看图识隐患)与高危底色(黄)缩短间隔;答错立即回炉(interval=1,
         ease 降档);"今日复习"队列到期优先、逾期越久越靠前。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import time

EASE_INIT_X100 = 250          # SM-2 EF 2.5 → 整数化 ×100
EASE_MIN_X100 = 130
EASE_GAIN_X100 = 10           # 答对增益
EASE_LAPSE_X100 = 20          # 答错降档
FIRST_INTERVAL_DAYS = 1
SECOND_INTERVAL_DAYS = 3
DAY_SECONDS = 86400

# 分层因子(×100 整数):难题型/高危底色 → 因子 <100 缩短间隔
TYPE_FACTOR_X100 = {"single": 100, "multi": 90, "judge": 110,
                    "risk": 70, "image": 80}
COLOR_FACTOR_X100 = {"none": 100, "yellow": 70, "cyan": 90, "green": 100}


def _iso(epoch: float) -> str:
    """@brief epoch → ISO 串"""
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).isoformat()


def _epoch(iso: str) -> float:
    """@brief ISO 串 → epoch"""
    return datetime.datetime.fromisoformat(iso).timestamp()


def next_interval_days(reps: int, prev_interval: int, ease_x100: int,
                       qtype: str, color: str) -> int:
    """
    @brief  SM-2 变体间隔(整数天):1 → 3 → 上轮×ease,再乘题型/底色分层因子
    @param  reps 本次答对后的连续答对次数(≥1)
    """
    if reps <= 1:
        base = FIRST_INTERVAL_DAYS
    elif reps == 2:
        base = SECOND_INTERVAL_DAYS
    else:
        base = max(1, (prev_interval * ease_x100) // 100)
    layered = (base * TYPE_FACTOR_X100[qtype] * COLOR_FACTOR_X100[color]
               ) // (100 * 100)
    return max(1, layered)


class SrsService:
    """按 owner 持久化的排期器(quiz_srs 表)。"""

    def __init__(self, db):
        self._db = db

    def _row(self, owner: str, question_id: int):
        rows = self._db.query(
            "SELECT id, ease_x100, interval_days, reps, lapses, due_at"
            " FROM quiz_srs WHERE owner = ? AND question_id = ?",
            (owner, question_id))
        return rows[0] if rows else None

    def feed(self, owner: str, question: dict, correct: bool,
             now: float = None) -> dict:
        """
        @brief  一次作答喂入:更新 ease/间隔/到期时刻
        @return {ease_x100, interval_days, reps, due_at}
        """
        now = time.time() if now is None else now
        row = self._row(owner, question["id"])
        if row is None:
            ease, interval, reps, lapses = EASE_INIT_X100, 0, 0, 0
            row_id = None
        else:
            row_id, ease, interval, reps, lapses, _ = row
        if correct:
            reps += 1
            ease = ease + EASE_GAIN_X100
            interval = next_interval_days(reps, interval, ease,
                                          question["qtype"],
                                          question["color"])
        else:
            reps = 0
            lapses += 1
            ease = max(EASE_MIN_X100, ease - EASE_LAPSE_X100)
            interval = FIRST_INTERVAL_DAYS       # 答错回炉:明日必复习
        due_at = _iso(now + interval * DAY_SECONDS)
        if row_id is None:
            self._db.execute(
                "INSERT INTO quiz_srs(owner, question_id, ease_x100,"
                " interval_days, reps, lapses, due_at) VALUES(?,?,?,?,?,?,?)",
                (owner, question["id"], ease, interval, reps, lapses, due_at))
        else:
            self._db.execute(
                "UPDATE quiz_srs SET ease_x100 = ?, interval_days = ?,"
                " reps = ?, lapses = ?, due_at = ? WHERE id = ?",
                (ease, interval, reps, lapses, due_at, row_id))
        return {"ease_x100": ease, "interval_days": interval, "reps": reps,
                "due_at": due_at}

    def due_queue(self, owner: str, now: float = None, limit: int = 50) -> list:
        """
        @brief  "今日复习"队列:到期题目(due_at ≤ now)按逾期时长降序,
                同逾期以 lapses 多者优先(错题权重,13-R-QZ-1)
        """
        now = time.time() if now is None else now
        rows = self._db.query(
            "SELECT s.question_id, s.due_at, s.lapses, s.reps, q.qno"
            " FROM quiz_srs s JOIN quiz_questions q ON q.id = s.question_id"
            " WHERE s.owner = ? AND s.due_at <= ?"
            " ORDER BY s.due_at ASC, s.lapses DESC LIMIT ?",
            (owner, _iso(now), limit))
        return [{"question_id": row[0], "due_at": row[1], "lapses": row[2],
                 "reps": row[3], "qno": row[4],
                 "overdue_seconds": max(0, int(now - _epoch(row[1])))}
                for row in rows]

    def state(self, owner: str, question_id: int):
        """@brief 单题排期态 @return dict|None"""
        row = self._row(owner, question_id)
        if row is None:
            return None
        return {"ease_x100": row[1], "interval_days": row[2], "reps": row[3],
                "lapses": row[4], "due_at": row[5]}
