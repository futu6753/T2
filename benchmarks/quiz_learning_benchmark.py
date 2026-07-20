# -*- coding: utf-8 -*-
"""
@file    quiz_learning_benchmark.py
@brief   13-B10 学习效果基线(模拟用户):以指数遗忘模型(复习成功 →
         记忆半衰期按 SRS ease 放大)对照两种复习策略——
         A. SM-2 变体调度(真实 SrsService,今日队列驱动);
         B. 固定轮转(同等每日预算,不看到期);
         产出 30 天后保持率代理指标对照表(仅提交脚本,不提交产物;
         真实培训前后测为 MAY,实施前须过 PIPL 影响评估,H02-E2)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/quiz_learning_benchmark.py
"""
import os
import random
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_storage import Database, apply_migrations            # noqa: E402

from apps.quiz.bank import get_question, seed_bank           # noqa: E402
from apps.quiz.srs import DAY_SECONDS, SrsService            # noqa: E402

STUDY_QUESTIONS = 48          # 学习集规模(分日引入:12 新题/日 × 4 天)
INTRO_PER_DAY = 12
DAILY_BUDGET = 12             # 每日复习预算(两策略同额)
HORIZON_DAYS = 30             # 观察窗
BASE_HALFLIFE_DAYS = 2.0      # 首学后的记忆半衰期
SPACING_GAIN_CAP = 3.0        # 间隔效应:成功回忆增益随间隔/半衰期比放大
T0 = 4000000.0


def _fresh_db() -> Database:
    """@brief 独立 SQLite 库(含全量迁移与题库 seed)"""
    path = os.path.join(tempfile.mkdtemp(prefix="b10_"), "quiz.db")
    db = Database(f"sqlite:///{path}")
    apply_migrations(db)
    seed_bank(db)
    return db


def recall_probability(halflife_days: float, elapsed_days: float) -> float:
    """@brief 指数遗忘:P = 2^(-Δt/半衰期)"""
    return 2 ** (-elapsed_days / max(halflife_days, 1e-6))


class SimLearner:
    """模拟学员:每题维护(半衰期, 上次复习日)。"""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.memory = {}            # qno -> [halflife, last_day]

    def first_study(self, qno: int, day: int):
        """@brief 首学建档"""
        self.memory[qno] = [BASE_HALFLIFE_DAYS, day]

    def review(self, qno: int, day: int) -> bool:
        """@brief 一次复习:按当前保持率抽样成败;成功按间隔效应放大半衰期
        (增益随 间隔/半衰期 比增大,封顶;失败重学回底)"""
        halflife, last_day = self.memory[qno]
        elapsed = max(0.5, day - last_day)
        p = recall_probability(halflife, elapsed)
        correct = self.rng.random() < p
        if correct:
            ratio = min(elapsed / halflife, SPACING_GAIN_CAP)
            self.memory[qno][0] = halflife * (1.3 + 0.7 * ratio)
        else:                        # 失败重学:半衰期回底
            self.memory[qno][0] = BASE_HALFLIFE_DAYS
        self.memory[qno][1] = day
        return correct

    def retention_at(self, day: int) -> float:
        """@brief 期末保持率代理:全学习集平均即时回忆概率"""
        values = [recall_probability(h, day - last)
                  for h, last in self.memory.values()]
        return sum(values) / len(values)


def run_condition(strategy: str, seed: int) -> dict:
    """
    @brief  单条件仿真 @param strategy srs|rotation
    @return {"strategy", "retention", "reviews", "accuracy"}
    """
    rng = random.Random(seed)
    learner = SimLearner(rng)
    db = _fresh_db()
    srs = SrsService(db)
    owner = f"sim:{strategy}:{seed}"
    questions = {qno: get_question(db, qno)
                 for qno in range(1, STUDY_QUESTIONS + 1)}
    reviews = hits = 0
    rotation = []
    cursor = 0
    for day in range(1, HORIZON_DAYS + 1):
        now = T0 + day * DAY_SECONDS
        if day <= STUDY_QUESTIONS // INTRO_PER_DAY:   # 分日引入新题
            for offset in range(INTRO_PER_DAY):
                qno = (day - 1) * INTRO_PER_DAY + offset + 1
                learner.first_study(qno, day)
                srs.feed(owner, questions[qno], True, now=now)
                rotation.append(qno)
        if strategy == "srs":
            todays = [item["qno"] for item in
                      srs.due_queue(owner, now=now, limit=DAILY_BUDGET)]
        else:                                   # 固定轮转:无视到期
            todays = [rotation[(cursor + idx) % len(rotation)]
                      for idx in range(min(DAILY_BUDGET, len(rotation)))]
            cursor = (cursor + DAILY_BUDGET) % len(rotation)
        for qno in todays:
            correct = learner.review(qno, day)
            reviews += 1
            hits += 1 if correct else 0
            srs.feed(owner, questions[qno], correct, now=now)
    db.close()
    return {"strategy": strategy,
            "retention": round(learner.retention_at(HORIZON_DAYS), 4),
            "reviews": reviews,
            "accuracy": round(hits / max(1, reviews), 4)}


def run(seeds=(1, 2, 3)) -> list:
    """@brief 多种子对照 @return 逐种子两条件结果表"""
    table = []
    for seed in seeds:
        table.append(run_condition("srs", seed))
        table.append(run_condition("rotation", seed))
    return table


def main():
    """@brief 打印对照表(B10 证据:同预算下 SRS 保持率应占优)"""
    rows = run()
    print("策略       种子内序  30 天保持率  复习次数  复习正确率")
    for index, row in enumerate(rows):
        print(f"{row['strategy']:<9}  {index // 2 + 1:>6}"
              f"  {row['retention']:>10}  {row['reviews']:>7}"
              f"  {row['accuracy']:>9}")
    srs_mean = sum(r["retention"] for r in rows
                   if r["strategy"] == "srs") / (len(rows) // 2)
    rot_mean = sum(r["retention"] for r in rows
                   if r["strategy"] == "rotation") / (len(rows) // 2)
    print(f"\n均值:SRS {srs_mean:.4f} vs 轮转 {rot_mean:.4f}"
          f"(差 {srs_mean - rot_mean:+.4f};同预算 {DAILY_BUDGET} 题/日,"
          f"{HORIZON_DAYS} 天窗)")


if __name__ == "__main__":
    main()
