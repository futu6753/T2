# -*- coding: utf-8 -*-
"""
@file    test_r_qz.py
@brief   研究项验收锚点(H09 K.2,测试名 MUST NOT 改):
         test_r_qz1_srs —— SM-2 变体排期正确性(增长/回炉/题型底色分层)
         与"今日复习"队列(到期过滤/逾期靠前/错题权重);
         test_r_qz2_elo —— 整数化 ELO 合成作答序列收敛(存储全 int);
         test_r_qz3_migrate —— 迁移码一次性/TTL/散列存储/防冒领全拒,
         合并零个人信息且数据正确求和。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import random
import unittest

from tests.quiz_env import QuizEnv, jbody

from apps.quiz import migrate as mig
from apps.quiz.bank import get_question
from apps.quiz.elo import AbilityService, expected_x1000
from apps.quiz.srs import (COLOR_FACTOR_X100, DAY_SECONDS, EASE_INIT_X100,
                           SrsService, TYPE_FACTOR_X100)

T0 = 3000000.0
OWNER = "sso:alice"


class RQz1SrsTest(unittest.TestCase):
    """13-R-QZ-1 间隔重复调度。"""

    def setUp(self):
        self.env = QuizEnv()
        self.srs = SrsService(self.env.db)

    def test_r_qz1_srs(self):
        """排期:1→3→ease 增长;答错回炉;题型/底色分层;今日队列语义"""
        db = self.env.db
        single = get_question(db, 1)          # single / none
        state = self.srs.feed(OWNER, single, True, now=T0)
        self.assertEqual(state["interval_days"], 1)
        self.assertEqual(state["reps"], 1)
        self.assertEqual(state["ease_x100"], EASE_INIT_X100 + 10)
        state = self.srs.feed(OWNER, single, True, now=T0 + DAY_SECONDS)
        self.assertEqual(state["interval_days"], 3)
        state = self.srs.feed(OWNER, single, True, now=T0 + 4 * DAY_SECONDS)
        self.assertEqual(state["interval_days"], (3 * 280) // 100)   # 8 天
        self.assertEqual(state["reps"], 3)
        # 答错回炉:reps 清零、ease 降 20、明日必复习
        state = self.srs.feed(OWNER, single, False, now=T0 + 5 * DAY_SECONDS)
        self.assertEqual(state["interval_days"], 1)
        self.assertEqual(state["reps"], 0)
        self.assertEqual(state["ease_x100"], 280 - 20)
        stored = self.srs.state(OWNER, single["id"])
        self.assertEqual(stored["lapses"], 1)
        self.assertIsInstance(stored["ease_x100"], int)   # 整数化(L1-06)
        # 分层:image 题型(因子 80)同历史下第二轮间隔短于 single(3→2 天)
        image = get_question(db, 201)         # image / none((201-1)%4=0)
        self.assertEqual((image["qtype"], image["color"]), ("image", "none"))
        self.srs.feed(OWNER, image, True, now=T0)
        layered = self.srs.feed(OWNER, image, True, now=T0 + DAY_SECONDS)
        self.assertEqual(layered["interval_days"],
                         (3 * TYPE_FACTOR_X100["image"]
                          * COLOR_FACTOR_X100["none"]) // 10000)
        self.assertLess(layered["interval_days"], 3)
        # 黄底(因子 70)进一步缩短:judge/yellow 第二轮 3*110*70//10000=2
        yellow = get_question(db, 130)        # judge / yellow((130-1)%4=1)
        self.assertEqual((yellow["qtype"], yellow["color"]),
                         ("judge", "yellow"))
        self.srs.feed(OWNER, yellow, True, now=T0)
        layered = self.srs.feed(OWNER, yellow, True, now=T0 + DAY_SECONDS)
        self.assertEqual(layered["interval_days"], 2)
        # 今日复习队列:到期过滤 + 逾期靠前 + 同期 lapses 优先
        queue_env = QuizEnv()
        queue_srs = SrsService(queue_env.db)
        early = get_question(queue_env.db, 10)
        late = get_question(queue_env.db, 11)
        future = get_question(queue_env.db, 12)
        lapsed = get_question(queue_env.db, 13)
        queue_srs.feed(OWNER, early, True, now=T0 - 5 * DAY_SECONDS)
        queue_srs.feed(OWNER, late, True, now=T0 - 2 * DAY_SECONDS)
        queue_srs.feed(OWNER, future, True, now=T0)          # 明日到期
        queue_srs.feed(OWNER, lapsed, False, now=T0 - 5 * DAY_SECONDS)
        queue = queue_srs.due_queue(OWNER, now=T0)
        qnos = [item["qno"] for item in queue]
        self.assertEqual(set(qnos), {10, 11, 13})            # 未到期不出
        self.assertEqual(qnos[0], 13)      # 同为 4 天前到期:lapses 多者优先
        self.assertEqual(qnos[1], 10)
        self.assertEqual(qnos[2], 11)                        # 逾期少者靠后
        self.assertGreater(queue[0]["overdue_seconds"],
                           queue[2]["overdue_seconds"])
        # 复习后离开队列
        queue_srs.feed(OWNER, early, True, now=T0)
        queue_srs.feed(OWNER, lapsed, True, now=T0)
        self.assertEqual([item["qno"] for item in
                          queue_srs.due_queue(OWNER, now=T0)], [11])


class RQz2EloTest(unittest.TestCase):
    """13-R-QZ-2 整数化 ELO 收敛。"""

    def test_r_qz2_elo(self):
        """合成作答序列(真实能力 1500)→ 估计收敛;存储/返回全 int"""
        env = QuizEnv()
        ability = AbilityService(env.db)
        true_rating = 1500
        rng = random.Random(20260719)
        for step in range(300):
            qno = rng.randrange(1, 234)
            question = get_question(env.db, qno)
            win_x1000 = expected_x1000(true_rating, question["difficulty"])
            correct = rng.randrange(1000) < win_x1000
            profile = ability.record(OWNER, question, correct)
            self.assertIsInstance(profile["rating"], int)
            self.assertIsInstance(profile["question_difficulty"], int)
        final = ability.get(OWNER)
        self.assertEqual(final["games"], 300)
        self.assertLess(abs(final["rating"] - true_rating), 150)   # 收敛带
        # 落库类型断言:SQLite 取回仍为 int(整数化存储,L1-06)
        row = env.db.query(
            "SELECT rating, games FROM quiz_ability WHERE owner = ?",
            (OWNER,))[0]
        self.assertIsInstance(row[0], int)
        difficulties = [r[0] for r in env.db.query(
            "SELECT difficulty FROM quiz_questions LIMIT 233")]
        self.assertTrue(all(isinstance(value, int)
                            for value in difficulties))
        # 高手作答后题目难度整体被压低(用户赢多 → 题目输分)
        self.assertLess(sum(difficulties) / len(difficulties), 1200)
        # 期望函数对称锚点(整数中间量)
        self.assertEqual(expected_x1000(1200, 1200), 500)
        self.assertEqual(expected_x1000(1600, 1200)
                         + expected_x1000(1200, 1600), 1000)


class RQz3MigrateTest(unittest.TestCase):
    """13-R-QZ-3 游客→SSO 无损迁移。"""

    def test_r_qz3_migrate(self):
        """一次性/TTL/散列/防冒领全拒;合并求和正确且游客侧清空"""
        env = QuizEnv()
        guest, guest_code = env.guest_client()
        answer = guest.get(
            "/api/questions/1?mode=recite").json()["question"]["answer"]
        jbody(guest, "/api/answer", {"qno": 1, "answer": answer,
                                     "mode": "quiz"})
        jbody(guest, "/api/answer", {"qno": 2, "answer": "Z", "mode": "quiz"})
        jbody(guest, "/api/answer", {"qno": 3, "answer": "Z", "mode": "quiz"})
        jbody(guest, "/api/prefs", {"elo_sampling": True})
        sso = env.sso_client()
        # SSO 侧同题已有进度:合并须求和而非覆盖
        jbody(sso, "/api/answer", {"qno": 2, "answer": "Z", "mode": "quiz"})
        issued = jbody(guest, "/api/migrate/code", {}).json()
        code = issued["code"]
        self.assertEqual(issued["ttl_seconds"], 15 * 60)
        # 散列存储:库中无明文
        stored = env.db.query(
            "SELECT code_hash FROM quiz_migrate_codes")[0][0]
        self.assertNotEqual(stored, code)
        self.assertEqual(len(stored), 64)                  # sha256 hex
        # 错码拒
        bad = jbody(sso, "/api/migrate/redeem", {"code": "WRONG123"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("不存在", bad.json()["error"])
        # 正码兑换成功:合并统计
        done = jbody(sso, "/api/migrate/redeem", {"code": code}).json()
        self.assertTrue(done["ok"])
        self.assertEqual(done["merged"]["progress"], 3)
        progress = sso.get("/api/progress").json()
        self.assertEqual(progress["attempted"], 3)         # qno1/2/3
        self.assertEqual(progress["wrong_total"], 3)       # 游2 + 本1
        self.assertEqual(progress["correct_total"], 1)
        self.assertEqual({item["qno"] for item in
                          sso.get("/api/wrongbook").json()["wrongbook"]},
                         {2, 3})
        self.assertEqual(sso.get("/api/prefs").json(),
                         {"elo_sampling": True})           # 偏好取或
        # 游客侧数据零残留(四张刷题表 + 游客号)
        for table in ("quiz_progress", "quiz_srs", "quiz_ability",
                      "quiz_prefs"):
            rows = env.db.query(
                f"SELECT COUNT(*) FROM {table} WHERE owner = ?",
                (f"guest:{guest_code}",))
            self.assertEqual(rows[0][0], 0, table)
        self.assertEqual(env.db.query(
            "SELECT COUNT(*) FROM quiz_guests WHERE guest_code = ?",
            (guest_code,))[0][0], 0)
        # 一次性:重放拒
        replay = jbody(sso, "/api/migrate/redeem", {"code": code})
        self.assertEqual(replay.status_code, 400)
        self.assertIn("已使用", replay.json()["error"])
        # TTL:过期码拒(库级显式时钟)
        env2 = QuizEnv()
        _, guest2 = env2.guest_client()
        old_code = mig.create_code(env2.db, guest2, now=T0)
        expired = mig.redeem(env2.db, old_code, OWNER,
                             now=T0 + mig.CODE_TTL_SECONDS + 1)
        self.assertFalse(expired["ok"])
        self.assertIn("过期", expired["error"])
        fresh = mig.redeem(env2.db, old_code, OWNER,
                           now=T0 + mig.CODE_TTL_SECONDS - 1)
        self.assertTrue(fresh["ok"])                       # 时限内可用


class RQzB10Test(unittest.TestCase):
    """13-B10 学习效果基线(模拟用户,脚本回环防腐化)。"""

    def test_b10_srs_beats_rotation_on_retention(self):
        """同预算下 SRS 调度 30 天保持率优于固定轮转(种子 1 确定性)"""
        from benchmarks.quiz_learning_benchmark import run
        rows = run(seeds=(1,))
        by_strategy = {row["strategy"]: row for row in rows}
        self.assertGreater(by_strategy["srs"]["retention"],
                           by_strategy["rotation"]["retention"])
        self.assertGreater(by_strategy["srs"]["reviews"], 0)
        self.assertLessEqual(by_strategy["srs"]["reviews"],
                             by_strategy["rotation"]["reviews"])


if __name__ == "__main__":
    unittest.main()
