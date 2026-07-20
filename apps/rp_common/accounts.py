# -*- coding: utf-8 -*-
"""
@file    accounts.py
@brief   RP 本地账户服务(H03 §1):SSO 首登自动建号(最小角色、无口令旁路、
         显示名冲突后缀)、重复登录固定映射、每次 SSO 登录刷新口令时间戳
         (06-E16)、四级角色字典、停用/锁定对 SSO 同样生效。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets
from datetime import datetime, timezone

from gd_common.errors import PolicyValidationError
from gd_common.jsonlog import get_logger
from gd_crypto import hash_password
from gd_storage import Database

_log = get_logger("rp.accounts")

# 统一四级角色字典(H03 §1),各子系统声明子集
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_AUDITOR = "auditor"
ROLE_USER = "user"

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

UNUSABLE_PASSWORD_PREFIX = "!sso-unusable!"     # SSO 用户无口令旁路(H03 §1)
DISPLAY_SUFFIX_LIMIT = 99                       # 显示名冲突后缀上限


def _now() -> str:
    """@brief UTC ISO 时间(H12 §二 时间列一律 UTC)"""
    return datetime.now(timezone.utc).isoformat()


def unusable_password_hash(suite) -> str:
    """
    @brief  生成"不可用口令"哈希:随机值前缀标记,任何口令都无法匹配,
            且不触发首登改密(06-E16)
    """
    random_tail = secrets.token_hex(16)
    return UNUSABLE_PASSWORD_PREFIX + hash_password(random_tail, suite)


class RpAccountService:
    """RP 本地账户目录(表名注入,四系统复用同一实现)。"""

    def __init__(self, db: Database, suite, table: str, allowed_roles: tuple,
                 default_role: str, has_token_valid_after: bool = False):
        """
        @brief  绑定本系统账户表
        @param  table                本地账户表名(cv_users/nvr_users/…)
        @param  allowed_roles        本系统角色子集(H03 §1)
        @param  default_role         SSO 建号默认角色(最小权限)
        @param  has_token_valid_after certvault JWT iat 吊销列开关
        """
        if default_role not in allowed_roles:
            raise PolicyValidationError(f"默认角色 {default_role} 不在角色子集内")
        self._db = db
        self._suite = suite
        self._table = table
        self._allowed_roles = allowed_roles
        self._default_role = default_role
        self._has_tva = has_token_valid_after

    # ---- 查询 ----------------------------------------------------------
    def _select_columns(self) -> str:
        """@brief 查询列(quiz 表无口令体系,以空串占位保持字典结构一致)"""
        password_col = "''" if self._table == "quiz_users" else "password_hash"
        return (f"id, username, display_name, role, sso_sub, status,"
                f" {password_col}")

    def get_by_username(self, username: str) -> dict:
        """@brief 按用户名取账户(不存在返回 None)"""
        rows = self._db.query(
            f"SELECT {self._select_columns()} FROM {self._table}"
            f" WHERE username = ?", (username,))
        return self._row_to_user(rows[0]) if rows else None

    def get_by_sub(self, sub: str) -> dict:
        """@brief 按 SSO sub 取账户(固定映射查询,09 §二 C.3)"""
        rows = self._db.query(
            f"SELECT {self._select_columns()} FROM {self._table}"
            f" WHERE sso_sub = ?", (sub,))
        return self._row_to_user(rows[0]) if rows else None

    def _row_to_user(self, row: tuple) -> dict:
        """@brief 行 → 账户字典"""
        return {"id": row[0], "username": row[1], "display_name": row[2],
                "role": row[3], "sso_sub": row[4], "status": row[5],
                "password_hash": row[6]}

    # ---- SSO 自动建号(H03 §1) ----------------------------------------
    def ensure_sso_account(self, claims: dict) -> dict:
        """
        @brief  SSO 登录账户映射:已映射→刷新口令时间戳后返回(06-E16);
                未映射→按 sub 建号(最小角色、无口令旁路、显示名冲突加后缀)
        @param  claims 验签后的 id_token 声明(sub/preferred_username)
        @return 本地账户字典
        @raise  PolicyValidationError 账户已停用(停用对 SSO 同样生效,H08 §3)
        """
        sub = claims["sub"]
        user = self.get_by_sub(sub)
        if user is None:
            user = self._create_sso_user(sub, claims.get("preferred_username", sub))
            _log.info("SSO 首登自动建号", extra={"ctx": {
                "table": self._table, "username": user["username"],
                "role": user["role"]}})
        if user["status"] != STATUS_ACTIVE:
            raise PolicyValidationError("账户已停用,禁止登录")
        self._touch_password_timestamp(user["id"])
        return user

    def _create_sso_user(self, sub: str, wanted_name: str) -> dict:
        """@brief 建号:用户名=sub;显示名冲突自动加后缀(H03 §1)"""
        display = self._dedupe_display_name(wanted_name or sub)
        now = _now()
        columns = ("username, display_name, password_hash, role, sso_sub,"
                   " status, password_changed_at, created_at")
        values = [sub, display, unusable_password_hash(self._suite),
                  self._default_role, sub, STATUS_ACTIVE, now, now]
        placeholders = "?, ?, ?, ?, ?, ?, ?, ?"
        if self._has_tva:
            columns += ", token_valid_after"
            values.append(now)
            placeholders += ", ?"
        if self._table == "quiz_users":     # quiz 表无口令列(仅 SSO/游客)
            columns = "username, display_name, role, sso_sub, status, created_at"
            values = [sub, display, self._default_role, sub, STATUS_ACTIVE, now]
            placeholders = "?, ?, ?, ?, ?, ?"
        self._db.execute(
            f"INSERT INTO {self._table}({columns}) VALUES({placeholders})",
            tuple(values))
        return self.get_by_sub(sub)

    def _dedupe_display_name(self, wanted: str) -> str:
        """@brief 显示名冲突自动加后缀 -2/-3/…(H03 §1)"""
        rows = self._db.query(
            f"SELECT COUNT(*) FROM {self._table} WHERE display_name = ?", (wanted,))
        if rows[0][0] == 0:
            return wanted
        for suffix in range(2, DISPLAY_SUFFIX_LIMIT + 1):
            candidate = f"{wanted}-{suffix}"
            rows = self._db.query(
                f"SELECT COUNT(*) FROM {self._table} WHERE display_name = ?",
                (candidate,))
            if rows[0][0] == 0:
                return candidate
        raise PolicyValidationError("显示名冲突后缀已用尽")

    def _touch_password_timestamp(self, user_id: int):
        """@brief 每次 SSO 登录刷新口令时间戳,防 90 天周期误伤(06-E16)"""
        if self._table == "quiz_users":
            return                          # quiz 无口令体系
        self._db.execute(
            f"UPDATE {self._table} SET password_changed_at = ? WHERE id = ?",
            (_now(), user_id))

    # ---- 管理操作 ------------------------------------------------------
    def set_role(self, username: str, role: str):
        """@brief 提权/降级(仅管理员操作,调用方负责鉴权与审计,H03 §1)"""
        if role not in self._allowed_roles:
            raise PolicyValidationError(f"角色 {role} 不在本系统子集内")
        self._db.execute(
            f"UPDATE {self._table} SET role = ? WHERE username = ?",
            (role, username))

    def set_status(self, username: str, status: str):
        """@brief 启停账户(停用对 SSO 同样生效)"""
        self._db.execute(
            f"UPDATE {self._table} SET status = ? WHERE username = ?",
            (status, username))

    def revoke_tokens(self, username: str):
        """
        @brief  certvault"重置口令/踢下线":刷新 token_valid_after,
                iat 早于该时刻的 JWT 全部拒绝(H03 §6 / 逐请求回库校验)
        """
        if not self._has_tva:
            raise PolicyValidationError("本系统不使用 JWT iat 吊销")
        self._db.execute(
            f"UPDATE {self._table} SET token_valid_after = ? WHERE username = ?",
            (_now(), username))

    def token_valid_after(self, username: str) -> str:
        """@brief 读 JWT 吊销水位(certvault 逐请求回库校验用)"""
        rows = self._db.query(
            f"SELECT token_valid_after FROM {self._table} WHERE username = ?",
            (username,))
        return rows[0][0] if rows else None
