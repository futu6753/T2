# -*- coding: utf-8 -*-
"""
@file    migrate.py
@brief   13-R-QZ-3 游客→SSO 无损迁移:一次性迁移码(明文仅发码时一次,
         库存 SHA-256 散列;TTL 15 分钟;用后作废;错码/过期/重放全拒);
         合并零个人信息——仅触达 quiz_progress/quiz_srs/quiz_ability/
         quiz_prefs 四张刷题数据表,合并后删除游客侧行与游客号。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import secrets
import time
from datetime import datetime, timezone

CODE_TTL_SECONDS = 15 * 60
CODE_BYTES = 6                # token_urlsafe(6) → 8 字符明文码


def _iso(epoch: float) -> str:
    """@brief epoch → ISO 串"""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _hash(code: str) -> str:
    """@brief 迁移码散列(库中不存明文)"""
    return hashlib.sha256(code.encode()).hexdigest()


def create_code(db, guest_code: str, now: float = None) -> str:
    """
    @brief  为游客号发一次性迁移码
    @return 明文码(仅此一次;响应后不可再取)
    """
    now = time.time() if now is None else now
    code = secrets.token_urlsafe(CODE_BYTES)
    db.execute(
        "INSERT INTO quiz_migrate_codes(code_hash, guest_code, created_at,"
        " expires_at) VALUES(?,?,?,?)",
        (_hash(code), guest_code, _iso(now), _iso(now + CODE_TTL_SECONDS)))
    return code


def redeem(db, code: str, sso_owner: str, now: float = None) -> dict:
    """
    @brief  SSO 侧兑换:校验散列/TTL/一次性 → 合并 → 作废码 → 删游客数据
    @return {"ok": True, "merged": {...}} 或 {"ok": False, "error": 人话}
    """
    now = time.time() if now is None else now
    rows = db.query(
        "SELECT id, guest_code, expires_at, used_at FROM quiz_migrate_codes"
        " WHERE code_hash = ?", (_hash(code or ""),))
    if not rows:
        return {"ok": False, "error": "迁移码不存在或不正确"}
    row_id, guest_code, expires_at, used_at = rows[0]
    if used_at:
        return {"ok": False, "error": "迁移码已使用(一次性,重放拒绝)"}
    if _iso(now) > expires_at:
        return {"ok": False, "error": "迁移码已过期,请在游客端重新生成"}
    guest_owner = f"guest:{guest_code}"
    merged = _merge(db, guest_owner, sso_owner)
    db.execute("UPDATE quiz_migrate_codes SET used_at = ? WHERE id = ?",
               (_iso(now), row_id))
    db.execute("DELETE FROM quiz_guests WHERE guest_code = ?", (guest_code,))
    return {"ok": True, "guest_code": guest_code, "merged": merged}


def _merge(db, guest_owner: str, sso_owner: str) -> dict:
    """
    @brief  仅刷题数据合并(零个人信息):
            progress 计数求和/错题本取并;srs 取 reps 更高(更熟)一方;
            ability 按局数加权平均(整数);prefs 取或。合并后删游客行。
    """
    moved = {"progress": 0, "srs": 0}
    for row in db.query(
            "SELECT question_id, correct_count, wrong_count, in_wrongbook,"
            " last_result, updated_at FROM quiz_progress WHERE owner = ?",
            (guest_owner,)):
        db.execute(
            "INSERT INTO quiz_progress(owner, question_id, correct_count,"
            " wrong_count, in_wrongbook, last_result, updated_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(owner, question_id) DO UPDATE SET"
            " correct_count = correct_count + ?, wrong_count = wrong_count + ?,"
            " in_wrongbook = MAX(in_wrongbook, ?), updated_at = ?",
            (sso_owner, row[0], row[1], row[2], row[3], row[4], row[5],
             row[1], row[2], row[3], row[5]))
        moved["progress"] += 1
    for row in db.query(
            "SELECT question_id, ease_x100, interval_days, reps, lapses,"
            " due_at FROM quiz_srs WHERE owner = ?", (guest_owner,)):
        existing = db.query(
            "SELECT id, reps FROM quiz_srs WHERE owner = ? AND"
            " question_id = ?", (sso_owner, row[0]))
        if not existing:
            db.execute(
                "INSERT INTO quiz_srs(owner, question_id, ease_x100,"
                " interval_days, reps, lapses, due_at) VALUES(?,?,?,?,?,?,?)",
                (sso_owner, row[0], row[1], row[2], row[3], row[4], row[5]))
            moved["srs"] += 1
        elif row[3] > existing[0][1]:          # 游客侧更熟:覆盖排期
            db.execute(
                "UPDATE quiz_srs SET ease_x100 = ?, interval_days = ?,"
                " reps = ?, lapses = ?, due_at = ? WHERE id = ?",
                (row[1], row[2], row[3], row[4], row[5], existing[0][0]))
            moved["srs"] += 1
    guest_ability = db.query(
        "SELECT rating, games FROM quiz_ability WHERE owner = ?",
        (guest_owner,))
    if guest_ability:
        g_rating, g_games = guest_ability[0]
        sso_ability = db.query(
            "SELECT rating, games FROM quiz_ability WHERE owner = ?",
            (sso_owner,))
        if sso_ability and (sso_ability[0][1] + g_games) > 0:
            s_rating, s_games = sso_ability[0]
            total = s_games + g_games
            rating = (s_rating * s_games + g_rating * g_games) // max(1, total)
            games = total
        else:
            rating, games = g_rating, g_games
        from apps.quiz.elo import AbilityService
        AbilityService(db).set_merged(sso_owner, rating, games)
        moved["ability"] = {"rating": int(rating), "games": int(games)}
    guest_prefs = db.query(
        "SELECT elo_sampling FROM quiz_prefs WHERE owner = ?", (guest_owner,))
    if guest_prefs and guest_prefs[0][0]:
        db.execute(
            "INSERT INTO quiz_prefs(owner, elo_sampling) VALUES(?,1)"
            " ON CONFLICT(owner) DO UPDATE SET elo_sampling = 1",
            (sso_owner,))
    for table in ("quiz_progress", "quiz_srs", "quiz_ability", "quiz_prefs"):
        db.execute(f"DELETE FROM {table} WHERE owner = ?",     # nosec 固定表名
                   (guest_owner,))
    return moved
