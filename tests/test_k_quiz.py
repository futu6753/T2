# -*- coding: utf-8 -*-
"""
@file    test_k_quiz.py
@brief   M7 主验收(H02-E1 + H03 §6):seed 幂等与题库分布(233/五题型/
         84 图/四底色)、双分类过滤、背题 vs 做题答案可见性、四类判分
         (风险问答关键词)、错题本增删与账号隔离、进度汇总、偏好开关、
         身份边界(游客发码/SSO 兑换/guest_mode 开关)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.quiz_env import QuizEnv, jbody

from apps.quiz.bank import build_bank, seed_bank


class QuizBankTest(unittest.TestCase):
    """题库与刷题基线。"""

    def setUp(self):
        """@brief 每用例独立环境(装配即 seed)"""
        self.env = QuizEnv()

    def test_seed_idempotent_and_distribution(self):
        """seed 幂等(重复导入零新增);分布 233=92+34+31+40+36;84 配图"""
        self.assertEqual(seed_bank(self.env.db), 0)      # 已 seed:零新增
        summary = self.env.client().get("/api/bank/summary").json()
        self.assertEqual(summary["total"], 233)
        self.assertEqual(summary["by_type"],
                         {"single": 92, "multi": 34, "judge": 31,
                          "risk": 40, "image": 36})
        self.assertEqual(summary["images"], 84)
        self.assertEqual(sum(summary["by_color"].values()), 233)
        self.assertEqual(set(summary["by_color"]),
                         {"none", "yellow", "cyan", "green"})
        # 生成器确定性:两次构建逐字节一致
        self.assertEqual(build_bank(), build_bank())
        # 看图识隐患全部配图
        rows = self.env.db.query(
            "SELECT COUNT(*) FROM quiz_questions"
            " WHERE qtype = 'image' AND image = ''")
        self.assertEqual(rows[0][0], 0)

    def test_filters_by_type_and_color(self):
        """双分类过滤:题型/底色独立与组合;非法枚举 400 人话"""
        anon = self.env.client()
        risk = anon.get("/api/questions?qtype=risk&limit=233").json()
        self.assertEqual(len(risk["questions"]), 40)
        self.assertTrue(all(item["qtype"] == "risk"
                            for item in risk["questions"]))
        yellow = anon.get("/api/questions?color=yellow&limit=233").json()
        self.assertEqual(len(yellow["questions"]), 58)
        combo = anon.get(
            "/api/questions?qtype=single&color=none&limit=233").json()
        self.assertTrue(all(item["qtype"] == "single"
                            and item["color"] == "none"
                            for item in combo["questions"]))
        self.assertEqual(
            anon.get("/api/questions?qtype=essay").status_code, 400)
        self.assertEqual(
            anon.get("/api/questions?color=red").status_code, 400)

    def test_recite_vs_quiz_answer_visibility(self):
        """背题模式带答案解析;做题模式隐藏;列表视图一律无答案"""
        guest, _ = self.env.guest_client()
        listing = guest.get("/api/questions?limit=3").json()["questions"]
        self.assertTrue(all("answer" not in item for item in listing))
        recite = guest.get("/api/questions/1?mode=recite").json()["question"]
        self.assertIn("answer", recite)
        self.assertIn("analysis", recite)
        quiz = guest.get("/api/questions/1?mode=quiz").json()["question"]
        self.assertNotIn("answer", quiz)
        # 背题模式提交:只回解析不计对错
        result = jbody(guest, "/api/answer",
                       {"qno": 1, "mode": "recite", "answer": ""}).json()
        self.assertIn("answer", result["question"])
        self.assertEqual(
            guest.get("/api/progress").json()["attempted"], 0)

    def test_grading_four_families(self):
        """判分:单选字母/多选集合(顺序无关)/判断对错/风险问答关键词命中"""
        guest, _ = self.env.guest_client()

        def answer_of(qno):
            return guest.get(
                f"/api/questions/{qno}?mode=recite").json()["question"]

        single = answer_of(1)                      # qno 1 = single
        ok = jbody(guest, "/api/answer",
                   {"qno": 1, "answer": single["answer"].lower(),
                    "mode": "quiz"}).json()
        self.assertTrue(ok["correct"])             # 大小写不敏感
        multi_qno = 93                             # 92 单选后首题为多选
        multi = answer_of(multi_qno)
        self.assertEqual(multi["qtype"], "multi")
        shuffled = multi["answer"][::-1]
        self.assertTrue(jbody(guest, "/api/answer",
                              {"qno": multi_qno, "answer": shuffled,
                               "mode": "quiz"}).json()["correct"])
        self.assertFalse(jbody(guest, "/api/answer",
                               {"qno": multi_qno,
                                "answer": multi["answer"][:-1],
                                "mode": "quiz"}).json()["correct"])
        judge_qno = 92 + 34 + 1                    # 首题判断
        judge = answer_of(judge_qno)
        self.assertEqual(judge["qtype"], "judge")
        self.assertTrue(jbody(guest, "/api/answer",
                              {"qno": judge_qno, "answer": judge["answer"],
                               "mode": "quiz"}).json()["correct"])
        risk_qno = 92 + 34 + 31 + 1                # 首题风险问答
        risk = answer_of(risk_qno)
        keyword = risk["answer"].split("|")[1]
        self.assertTrue(jbody(guest, "/api/answer",
                              {"qno": risk_qno,
                               "answer": f"应当立即{keyword}并上报",
                               "mode": "quiz"}).json()["correct"])
        self.assertFalse(jbody(guest, "/api/answer",
                               {"qno": risk_qno, "answer": "不知道",
                                "mode": "quiz"}).json()["correct"])
        missing = jbody(guest, "/api/answer",
                        {"qno": 9999, "answer": "A", "mode": "quiz"})
        self.assertEqual(missing.status_code, 404)

    def test_wrongbook_lifecycle_and_owner_isolation(self):
        """错题自动入本;掌握后可清;guest 与 sso 账本互不可见"""
        guest, _ = self.env.guest_client()
        jbody(guest, "/api/answer", {"qno": 5, "answer": "Z", "mode": "quiz"})
        jbody(guest, "/api/answer", {"qno": 6, "answer": "Z", "mode": "quiz"})
        book = guest.get("/api/wrongbook").json()["wrongbook"]
        self.assertEqual({item["qno"] for item in book}, {5, 6})
        # 后又答对:计数更新但仍在错题本(须手动清除)
        answer = guest.get(
            "/api/questions/5?mode=recite").json()["question"]["answer"]
        jbody(guest, "/api/answer", {"qno": 5, "answer": answer,
                                     "mode": "quiz"})
        book = guest.get("/api/wrongbook").json()["wrongbook"]
        self.assertIn(5, {item["qno"] for item in book})
        self.assertEqual(
            jbody(guest, "/api/wrongbook/5/clear", {}).json(), {"ok": True})
        book = guest.get("/api/wrongbook").json()["wrongbook"]
        self.assertEqual({item["qno"] for item in book}, {6})
        # SSO 账号错题本独立
        sso = self.env.sso_client()
        self.assertEqual(sso.get("/api/wrongbook").json()["wrongbook"], [])
        jbody(sso, "/api/answer", {"qno": 7, "answer": "Z", "mode": "quiz"})
        self.assertEqual({item["qno"] for item in
                          sso.get("/api/wrongbook").json()["wrongbook"]}, {7})
        self.assertEqual({item["qno"] for item in
                          guest.get("/api/wrongbook").json()["wrongbook"]},
                         {6})

    def test_progress_summary_and_prefs(self):
        """进度汇总(计数/评分整数);偏好默认关、可开且按账号隔离"""
        guest, _ = self.env.guest_client()
        answer = guest.get(
            "/api/questions/1?mode=recite").json()["question"]["answer"]
        jbody(guest, "/api/answer", {"qno": 1, "answer": answer,
                                     "mode": "quiz"})
        jbody(guest, "/api/answer", {"qno": 2, "answer": "Z", "mode": "quiz"})
        progress = guest.get("/api/progress").json()
        self.assertEqual(progress["attempted"], 2)
        self.assertEqual(progress["correct_total"], 1)
        self.assertEqual(progress["wrong_total"], 1)
        self.assertEqual(progress["wrongbook"], 1)
        self.assertIsInstance(progress["rating"], int)   # 整数化(L1-06)
        self.assertEqual(guest.get("/api/prefs").json(),
                         {"elo_sampling": False})         # 默认关
        self.assertEqual(jbody(guest, "/api/prefs",
                               {"elo_sampling": True}).json(),
                         {"elo_sampling": True})
        sso = self.env.sso_client()
        self.assertEqual(sso.get("/api/prefs").json(),
                         {"elo_sampling": False})         # 账号隔离

    def test_identity_boundaries_and_guest_switch(self):
        """匿名访问受限;游客发码/SSO 兑换的身份边界;guest_mode 关闭形态"""
        anon = self.env.client()
        self.assertIn(anon.get("/api/progress").status_code, (302, 401))
        guest, _ = self.env.guest_client()
        sso = self.env.sso_client()
        # SSO 不能发码;游客不能兑换
        self.assertEqual(
            jbody(sso, "/api/migrate/code", {}).status_code, 403)
        self.assertEqual(
            jbody(guest, "/api/migrate/redeem", {"code": "x"}).status_code,
            403)
        # 顺序出题:游客答过 qno1 后 next 给 2
        answer = guest.get(
            "/api/questions/1?mode=recite").json()["question"]["answer"]
        jbody(guest, "/api/answer", {"qno": 1, "answer": answer,
                                     "mode": "quiz"})
        self.assertEqual(
            guest.get("/api/practice/next").json()["qno"], 2)
        # 邻域采样未开偏好 → 400 人话
        blocked = guest.get("/api/practice/next?strategy=neighborhood")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("默认关", blocked.json()["error"])
        jbody(guest, "/api/prefs", {"elo_sampling": True})
        picked = guest.get("/api/practice/next?strategy=neighborhood").json()
        self.assertIsInstance(picked["qno"], int)
        self.assertIsInstance(picked["rating"], int)
        # guest_mode 关闭:游客入口 403,SSO 正常
        closed = QuizEnv(guest_mode_enabled=False)
        self.assertEqual(
            closed.client().request("POST", "/guest/new").status_code, 403)
        self.assertEqual(
            closed.sso_client().get("/api/progress").status_code, 200)


if __name__ == "__main__":
    unittest.main()
