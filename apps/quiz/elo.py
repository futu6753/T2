# -*- coding: utf-8 -*-
"""
@file    elo.py
@brief   13-R-QZ-2 掌握度画像:简化 ELO 同时估计用户能力与题目难度。
         评分整数化存储与整数比较(H07 L1-06 禁浮点相等;期望胜率仅作
         内部中间量,落库/返回全为 int);"按能力邻域采样"出题策略
         (默认关,per-owner 偏好开启)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
RATING_INIT = 1200
K_USER = 32                   # 用户能力步长
K_QUESTION = 16               # 题目难度步长(更稳)
EXPECTED_SCALE = 1000         # 期望胜率整数化:×1000


def expected_x1000(rating_a: int, rating_b: int) -> int:
    """@brief A 对 B 的期望胜率(×1000 整数,logistic 内部中间量)"""
    return round(EXPECTED_SCALE / (1 + 10 ** ((rating_b - rating_a) / 400)))

def update(user_rating: int, question_rating: int, correct: bool) -> tuple:
    """
    @brief  一次作答后的双向更新(整数):答对视作用户胜,答错题目胜
    @return (new_user_rating, new_question_rating)
    """
    exp_user = expected_x1000(user_rating, question_rating)
    score = EXPECTED_SCALE if correct else 0
    delta_user = (K_USER * (score - exp_user)) // EXPECTED_SCALE
    delta_question = (K_QUESTION * (exp_user - score)) // EXPECTED_SCALE
    return int(user_rating + delta_user), int(question_rating + delta_question)


class AbilityService:
    """用户能力档案(quiz_ability 表)。"""

    def __init__(self, db):
        self._db = db

    def get(self, owner: str) -> dict:
        """@brief 读能力档案(缺省 1200/0)"""
        rows = self._db.query(
            "SELECT rating, games FROM quiz_ability WHERE owner = ?", (owner,))
        if not rows:
            return {"rating": RATING_INIT, "games": 0}
        return {"rating": rows[0][0], "games": rows[0][1]}

    def record(self, owner: str, question: dict, correct: bool) -> dict:
        """
        @brief  作答落账:更新用户能力与题目难度(整数双向)
        @return {"rating": 新能力, "games": 局数, "question_difficulty": 新难度}
        """
        profile = self.get(owner)
        new_user, new_question = update(profile["rating"],
                                        question["difficulty"], correct)
        self._db.execute(
            "INSERT INTO quiz_ability(owner, rating, games) VALUES(?,?,1)"
            " ON CONFLICT(owner) DO UPDATE SET rating = ?, games = games + 1",
            (owner, new_user, new_user))
        self._db.execute(
            "UPDATE quiz_questions SET difficulty = ? WHERE id = ?",
            (new_question, question["id"]))
        return {"rating": new_user, "games": profile["games"] + 1,
                "question_difficulty": new_question}

    def set_merged(self, owner: str, rating: int, games: int):
        """@brief 迁移合并写入(13-R-QZ-3)"""
        self._db.execute(
            "INSERT INTO quiz_ability(owner, rating, games) VALUES(?,?,?)"
            " ON CONFLICT(owner) DO UPDATE SET rating = ?, games = ?",
            (owner, int(rating), int(games), int(rating), int(games)))


def pick_neighborhood(db, owner: str, rating: int, limit: int = 1) -> list:
    """
    @brief  邻域采样:在"未作答或已到期"的题中选 |difficulty-rating| 最近者
            (整数距离排序,同距按 qno 升序保证确定性)
    """
    rows = db.query(
        "SELECT q.qno, q.difficulty FROM quiz_questions q"
        " LEFT JOIN quiz_progress p"
        "   ON p.question_id = q.id AND p.owner = ?"
        " WHERE p.id IS NULL OR p.last_result = 'wrong'"
        " ORDER BY ABS(q.difficulty - ?) ASC, q.qno ASC LIMIT ?",
        (owner, int(rating), limit))
    return [{"qno": row[0], "difficulty": row[1]} for row in rows]
