# -*- coding: utf-8 -*-
"""
@file    web.py
@brief   安全刷题应用(里程碑 7 全功能):题库 233 题五题型/双分类刷题、
         背题/做题双模式、错题本与进度按账号持久化、R-QZ-1 今日复习队列、
         R-QZ-2 整数 ELO 画像与邻域采样(默认关)、R-QZ-3 一次性迁移码。
         双身份体系(H03 §6):SSO 统一登录 + 5 位数字游客 ID(quiz_guest_mode
         独立开关,默认 true;仅刷题数据不涉个人信息);游客→SSO 无损迁移
         (一次性迁移码)随 13-R-QZ-3 在里程碑 7 交付。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.rp_common.accounts import ROLE_ADMIN, ROLE_USER, RpAccountService
from apps.rp_common.sso_routes import build_sso_router, require_session

from apps.quiz import migrate as mig
from apps.quiz.bank import seed_bank
from apps.quiz.elo import pick_neighborhood
from apps.quiz.practice import MODE_QUIZ, MODE_RECITE, PracticeService
from apps.quiz.srs import SrsService

SYSTEM = "quiz"
COOKIE_NAME = "gd_quiz_sid"
GUEST_COOKIE = "gd_quiz_guest"
GUEST_CODE_DIGITS = 5
GUEST_CODE_ATTEMPTS = 50           # 5 位数字空间冲突重试上限


def create_app(db, suite, sso, guest_mode_enabled: bool = True) -> FastAPI:
    """
    @brief  装配 quiz 骨架应用
    @param  guest_mode_enabled quiz_guest_mode 开关(H03 §6,默认 true)
    """
    app = FastAPI(title="港电 安全刷题", docs_url=None, redoc_url=None)
    accounts = RpAccountService(db, suite, table="quiz_users",
                                allowed_roles=(ROLE_ADMIN, ROLE_USER),
                                default_role=ROLE_USER)
    cookie = sso.config.cookie_name or COOKIE_NAME
    app.include_router(build_sso_router(sso, accounts, cookie_name=cookie,
                                        cookie_secure=sso.config.cookie_secure))
    app.state.accounts = accounts

    def _new_guest_code() -> str:
        """@brief 分配未占用的 5 位数字游客 ID(对外业务码,H12 §二)"""
        for _ in range(GUEST_CODE_ATTEMPTS):
            code = str(secrets.randbelow(90000) + 10000)
            rows = db.query("SELECT COUNT(*) FROM quiz_guests"
                            " WHERE guest_code = ?", (code,))
            if rows[0][0] == 0:
                return code
        raise RuntimeError("游客 ID 空间耗尽,请清理或扩容")

    @app.post("/guest/new")
    def guest_new():
        """@brief 首次访问自动分配游客 ID(仅刷题数据,不涉个人信息)"""
        if not guest_mode_enabled:
            return JSONResponse({"error": "游客模式已关闭(quiz_guest_mode)"},
                                status_code=403)
        code = _new_guest_code()
        db.execute("INSERT INTO quiz_guests(guest_code, created_at) VALUES(?, ?)",
                   (code, datetime.now(timezone.utc).isoformat()))
        response = JSONResponse({"guest_code": code})
        response.set_cookie(GUEST_COOKIE, code, httponly=True, samesite="lax")
        return response

    @app.get("/guest/load/{code}")
    def guest_load(code: str):
        """@brief 输 ID 载入进度(进度数据随里程碑 7)"""
        if not guest_mode_enabled:
            return JSONResponse({"error": "游客模式已关闭(quiz_guest_mode)"},
                                status_code=403)
        rows = db.query("SELECT id FROM quiz_guests WHERE guest_code = ?", (code,))
        if not rows:
            return JSONResponse({"error": "游客 ID 不存在"}, status_code=404)
        return {"guest_code": code, "progress": "占位(里程碑 7 交付)"}

    @app.get("/me")
    def whoami(request: Request):
        """@brief 当前身份:SSO 会话优先,其次游客 Cookie"""
        user, error = require_session(request, sso, accounts, cookie_name=cookie)
        if user is not None:
            return {"kind": "sso", "username": user["username"],
                    "role": user["role"]}
        guest = request.cookies.get(GUEST_COOKIE, "")
        if guest and guest_mode_enabled:
            return {"kind": "guest", "guest_code": guest}
        return error

    @app.get("/healthz")
    def healthz():
        """@brief 健康检查"""
        return {"status": "ok", "system": SYSTEM,
                "sso_enabled": sso.status()["enabled"],
                "guest_mode": guest_mode_enabled}

    # ================= 里程碑 7:刷题业务面 =================
    seed_bank(db)                      # 幂等导入 233 题(H02-E1)
    practice = PracticeService(db)
    srs = SrsService(db)
    app.state.practice = practice

    def _owner(request: Request):
        """@brief 统一身份归一:SSO 优先 → "sso:<用户名>";游客 → "guest:<ID>"
        @return (owner, kind, error_response)"""
        user, error = require_session(request, sso, accounts,
                                      cookie_name=cookie)
        if user is not None:
            return f"sso:{user['username']}", "sso", None
        guest = request.cookies.get(GUEST_COOKIE, "")
        if guest and guest_mode_enabled:
            rows = db.query("SELECT id FROM quiz_guests WHERE guest_code = ?",
                            (guest,))
            if rows:
                return f"guest:{guest}", "guest", None
        return None, "", error

    @app.get("/api/bank/summary")
    def bank_summary():
        """@brief 题库分布(题型/底色/配图,公开可看)"""
        return practice.bank_summary()

    @app.get("/api/questions")
    def list_questions(qtype: str = "", color: str = "", limit: int = 50,
                       offset: int = 0):
        """@brief 列表视图(双分类过滤;不含答案)"""
        if qtype and qtype not in ("single", "multi", "judge", "risk",
                                   "image"):
            return JSONResponse({"error": "题型须为五类之一"}, status_code=400)
        if color and color not in ("none", "yellow", "cyan", "green"):
            return JSONResponse({"error": "底色须为 无色/黄/青/绿"},
                                status_code=400)
        return {"questions": practice.list_questions(qtype, color, limit,
                                                     offset)}

    @app.get("/api/questions/{qno}")
    def one_question(qno: int, request: Request, mode: str = MODE_QUIZ):
        """@brief 单题视图:背题模式含答案解析;做题模式隐藏(须登录/游客)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        from apps.quiz.bank import get_question
        from apps.quiz.practice import public_view
        question = get_question(db, qno)
        if question is None:
            return JSONResponse({"error": "题目不存在"}, status_code=404)
        return {"mode": mode, "question": public_view(
            question, with_answer=(mode == MODE_RECITE))}

    @app.post("/api/answer")
    async def submit_answer(request: Request):
        """@brief 提交作答(背题=只回解析;做题=判分并落全链)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        import json as _json
        try:
            body = _json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        mode = body.get("mode", MODE_QUIZ)
        if mode not in (MODE_QUIZ, MODE_RECITE):
            return JSONResponse({"error": "mode 须为 quiz 或 recite"},
                                status_code=400)
        result = practice.submit(owner, int(body.get("qno", 0)),
                                 str(body.get("answer", "")), mode)
        if "error" in result:
            return JSONResponse({"error": result["error"]},
                                status_code=result.get("status", 400))
        return result

    @app.get("/api/wrongbook")
    def wrongbook(request: Request):
        """@brief 错题本(按账号隔离)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        return {"wrongbook": practice.wrongbook(owner)}

    @app.post("/api/wrongbook/{qno}/clear")
    def wrongbook_clear(qno: int, request: Request):
        """@brief 掌握后移出错题本"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        if not practice.clear_wrong(owner, qno):
            return JSONResponse({"error": "题目不存在"}, status_code=404)
        return {"ok": True}

    @app.get("/api/progress")
    def progress(request: Request):
        """@brief 进度汇总(含整数能力评分)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        return practice.progress_summary(owner)

    @app.get("/api/review/today")
    def review_today(request: Request, limit: int = 50):
        """@brief "今日复习"队列(R-QZ-1:到期优先,逾期靠前)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        return {"queue": srs.due_queue(owner, limit=limit)}

    @app.get("/api/prefs")
    def get_prefs(request: Request):
        """@brief per-owner 偏好(邻域采样默认关)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        return practice.get_prefs(owner)

    @app.post("/api/prefs")
    async def set_prefs(request: Request):
        """@brief 写偏好(R-QZ-2:用户可开)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        import json as _json
        try:
            body = _json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        return practice.set_prefs(owner, bool(body.get("elo_sampling")))

    @app.get("/api/practice/next")
    def practice_next(request: Request, strategy: str = "sequence"):
        """@brief 出题策略:sequence 顺序;neighborhood 邻域采样(须开偏好)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        if strategy == "neighborhood":
            if not practice.get_prefs(owner)["elo_sampling"]:
                return JSONResponse(
                    {"error": "邻域采样未开启(默认关,请先在偏好中开启)"},
                    status_code=400)
            rating = practice.ability.get(owner)["rating"]
            picked = pick_neighborhood(db, owner, rating, limit=1)
            if not picked:
                return JSONResponse({"error": "暂无可采样题目"},
                                    status_code=404)
            return {"strategy": strategy, "qno": picked[0]["qno"],
                    "difficulty": picked[0]["difficulty"], "rating": rating}
        rows = db.query(
            "SELECT q.qno FROM quiz_questions q LEFT JOIN quiz_progress p"
            " ON p.question_id = q.id AND p.owner = ?"
            " WHERE p.id IS NULL ORDER BY q.qno LIMIT 1", (owner,))
        if not rows:
            return {"strategy": "sequence", "qno": None, "done": True}
        return {"strategy": "sequence", "qno": rows[0][0]}

    @app.post("/api/migrate/code")
    def migrate_code(request: Request):
        """@brief 发一次性迁移码(仅游客身份;明文仅此一次,R-QZ-3)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        if kind != "guest":
            return JSONResponse(
                {"error": "仅游客身份可生成迁移码(SSO 侧负责兑换)"},
                status_code=403)
        code = mig.create_code(db, owner.split(":", 1)[1])
        return {"code": code, "ttl_seconds": mig.CODE_TTL_SECONDS,
                "note": "明文仅展示一次,请在 SSO 登录后 15 分钟内兑换"}

    @app.post("/api/migrate/redeem")
    async def migrate_redeem(request: Request):
        """@brief SSO 侧兑换迁移码(一次性/TTL/散列校验,合并零个人信息)"""
        owner, kind, error = _owner(request)
        if owner is None:
            return error
        if kind != "sso":
            return JSONResponse({"error": "仅 SSO 登录身份可兑换迁移码"},
                                status_code=403)
        import json as _json
        try:
            body = _json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        result = mig.redeem(db, str(body.get("code", "")), owner)
        if not result["ok"]:
            return JSONResponse({"error": result["error"]}, status_code=400)
        return result

    return app
