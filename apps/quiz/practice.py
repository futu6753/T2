# -*- coding: utf-8 -*-
"""
@file    practice.py
@brief   刷题服务(H02-E1):题目列表(题型/底色过滤,列表视图不含答案)、
         背题模式(直出答案解析不计对错)/做题模式(判分→进度→错题本→
         SRS→ELO 一条龙)、错题本增删、进度汇总、per-owner 偏好
         (elo_sampling 邻域采样开关,默认关)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from datetime import datetime, timezone

from apps.quiz import bank
from apps.quiz.elo import AbilityService
from apps.quiz.srs import SrsService

MODE_RECITE = "recite"        # 背题:直接展示答案解析,不计对错
MODE_QUIZ = "quiz"            # 做题:判分,错题自动入错题本


def _now_iso() -> str:
    """@brief UTC ISO 时间"""
    return datetime.now(timezone.utc).isoformat()


def public_view(question: dict, with_answer: bool) -> dict:
    """@brief 对外题目视图(做题模式不带答案/解析)"""
    view = {"qno": question["qno"], "qtype": question["qtype"],
            "color": question["color"], "stem": question["stem"],
            "options": question["options"], "image": question["image"]}
    if with_answer:
        view["answer"] = question["answer"]
        view["analysis"] = question["analysis"]
    return view


class PracticeService:
    """按 owner 的刷题一条龙。"""

    def __init__(self, db):
        self._db = db
        self.srs = SrsService(db)
        self.ability = AbilityService(db)

    # ---- 题目视图 ------------------------------------------------------
    def list_questions(self, qtype: str = "", color: str = "",
                       limit: int = 50, offset: int = 0) -> list:
        """@brief 列表视图(不含答案;题型/底色可组合过滤)"""
        conditions, params = [], []
        if qtype:
            conditions.append("qtype = ?")
            params.append(qtype)
        if color:
            conditions.append("color = ?")
            params.append(color)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([max(1, min(int(limit), 233)), max(0, int(offset))])
        rows = self._db.query(
            "SELECT qno, qtype, color, stem, image FROM quiz_questions"
            f"{where} ORDER BY qno LIMIT ? OFFSET ?", tuple(params))
        return [{"qno": row[0], "qtype": row[1], "color": row[2],
                 "stem": row[3], "image": row[4]} for row in rows]

    def bank_summary(self) -> dict:
        """@brief 题库分布(题型/底色计数 + 配图数)"""
        by_type = {row[0]: row[1] for row in self._db.query(
            "SELECT qtype, COUNT(*) FROM quiz_questions GROUP BY qtype")}
        by_color = {row[0]: row[1] for row in self._db.query(
            "SELECT color, COUNT(*) FROM quiz_questions GROUP BY color")}
        total = self._db.query("SELECT COUNT(*) FROM quiz_questions")[0][0]
        images = self._db.query(
            "SELECT COUNT(*) FROM quiz_questions WHERE image != ''")[0][0]
        return {"total": total, "by_type": by_type, "by_color": by_color,
                "images": images}

    # ---- 作答 ----------------------------------------------------------
    def submit(self, owner: str, qno: int, answer: str, mode: str,
               now: float = None) -> dict:
        """
        @brief  提交作答:背题模式仅回答案解析;做题模式判分并落
                进度/错题本/SRS/ELO(H02-E1 + R-QZ-1/2)
        """
        question = bank.get_question(self._db, qno)
        if question is None:
            return {"error": "题目不存在", "status": 404}
        if mode == MODE_RECITE:
            return {"mode": mode,
                    "question": public_view(question, with_answer=True)}
        correct = bank.grade(question, answer)
        result = "correct" if correct else "wrong"
        self._db.execute(
            "INSERT INTO quiz_progress(owner, question_id, correct_count,"
            " wrong_count, in_wrongbook, last_result, updated_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(owner, question_id) DO UPDATE SET"
            " correct_count = correct_count + ?,"
            " wrong_count = wrong_count + ?,"
            " in_wrongbook = CASE WHEN ? = 1 THEN 1 ELSE in_wrongbook END,"
            " last_result = ?, updated_at = ?",
            (owner, question["id"], 1 if correct else 0, 0 if correct else 1,
             0 if correct else 1, result, _now_iso(),
             1 if correct else 0, 0 if correct else 1,
             0 if correct else 1, result, _now_iso()))
        schedule = self.srs.feed(owner, question, correct, now=now)
        profile = self.ability.record(owner, question, correct)
        return {"mode": mode, "qno": qno, "correct": correct,
                "answer": question["answer"], "analysis": question["analysis"],
                "srs": schedule, "ability": profile}

    # ---- 错题本 --------------------------------------------------------
    def wrongbook(self, owner: str) -> list:
        """@brief 错题本(按最近更新降序)"""
        rows = self._db.query(
            "SELECT q.qno, q.qtype, q.color, q.stem, p.wrong_count,"
            " p.updated_at FROM quiz_progress p"
            " JOIN quiz_questions q ON q.id = p.question_id"
            " WHERE p.owner = ? AND p.in_wrongbook = 1"
            " ORDER BY p.updated_at DESC", (owner,))
        return [{"qno": row[0], "qtype": row[1], "color": row[2],
                 "stem": row[3], "wrong_count": row[4], "updated_at": row[5]}
                for row in rows]

    def clear_wrong(self, owner: str, qno: int) -> bool:
        """@brief 移出错题本(掌握后手动清除)"""
        question = bank.get_question(self._db, qno)
        if question is None:
            return False
        self._db.execute(
            "UPDATE quiz_progress SET in_wrongbook = 0"
            " WHERE owner = ? AND question_id = ?", (owner, question["id"]))
        return True

    # ---- 进度与偏好 ----------------------------------------------------
    def progress_summary(self, owner: str) -> dict:
        """@brief 进度汇总(做题数/正确率/错题本量/能力评分)"""
        row = self._db.query(
            "SELECT COUNT(*), COALESCE(SUM(correct_count), 0),"
            " COALESCE(SUM(wrong_count), 0),"
            " COALESCE(SUM(in_wrongbook), 0)"
            " FROM quiz_progress WHERE owner = ?", (owner,))[0]
        profile = self.ability.get(owner)
        return {"attempted": row[0], "correct_total": row[1],
                "wrong_total": row[2], "wrongbook": row[3],
                "rating": profile["rating"], "games": profile["games"]}

    def get_prefs(self, owner: str) -> dict:
        """@brief per-owner 偏好(邻域采样默认关,R-QZ-2)"""
        rows = self._db.query(
            "SELECT elo_sampling FROM quiz_prefs WHERE owner = ?", (owner,))
        return {"elo_sampling": bool(rows[0][0]) if rows else False}

    def set_prefs(self, owner: str, elo_sampling: bool) -> dict:
        """@brief 写偏好"""
        self._db.execute(
            "INSERT INTO quiz_prefs(owner, elo_sampling) VALUES(?,?)"
            " ON CONFLICT(owner) DO UPDATE SET elo_sampling = ?",
            (owner, 1 if elo_sampling else 0, 1 if elo_sampling else 0))
        return {"elo_sampling": bool(elo_sampling)}
