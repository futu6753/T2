# -*- coding: utf-8 -*-
"""
@file    accounts.py
@brief   IdP 账户服务(H03/H04/H05):建号与口令策略、口令登录+锁定(Redis 计数,
         两步验证失败同计次)、TOTP、短信验证码(D2/D3 简化仅经 SecurityProfile)、
         演示账号种子/停用、管理员解锁。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import re
import secrets

from gd_common.errors import PolicyValidationError
from gd_crypto import (
    encrypt_envelope, envelope_from_json, envelope_to_json, decrypt_envelope,
    hash_password, hmac_index,
)
from gd_crypto.password import verify_password
from gd_policy.profile import is_test_code_accepted, SecurityProfile
from gd_storage import events, make_key
from apps.idp import totp as totp_mod

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_DEMO = "demo"
LOGIN_OK = "ok"
LOGIN_FAILED = "failed"          # 统一"用户名或口令错误"(H04 §五 鉴别信息)
LOGIN_LOCKED = "locked"
LOGIN_NEED_TOTP = "need_totp"
LOGIN_MUST_CHANGE = "must_change_password"
SMS_CODE_TTL_SECONDS = 300       # 短信验证码 5 分钟有效(02-A2)
SMS_CODE_DIGITS = 6
DEMO_SEED_ACCOUNTS = (           # H05-D1:演示种子账号(状态=demo)
    ("admin@example.com", "演示管理员", True),
    ("demo@example.com", "演示用户", False),
)
DEMO_SEED_PASSWORD = "Demo@2026#Test"    # 满足三类字符;生产态账号被停用
_COMPLEXITY_CLASSES = (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^a-zA-Z0-9]")


def _now_iso() -> str:
    """@brief 当前 UTC 时间 ISO 串"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate_password_policy(password: str, profile: SecurityProfile):
    """@brief 口令策略校验(注册/改密/建号统一入口,H03 §2)"""
    if len(password) < profile.password_min_length:
        raise PolicyValidationError(
            f"口令长度不得小于 {profile.password_min_length} 位")
    matched = sum(1 for pattern in _COMPLEXITY_CLASSES if re.search(pattern, password))
    required = 4 if profile.password_complexity == "four_classes" else 3
    if matched < required:
        raise PolicyValidationError(f"口令须至少包含 {required} 类字符(大小写/数字/符号)")


class AccountService:
    """统一用户目录操作(ARC-1 唯一主源)。"""

    def __init__(self, db, ring, suite, store, audit):
        """@brief 注入存储/密钥环/套件/易失态/审计"""
        self._db, self._ring, self._suite = db, ring, suite
        self._store, self._audit = store, audit

    # ---- 建号与查询 -------------------------------------------------------
    def create_user(self, account: str, display_name: str, password: str,
                    profile: SecurityProfile, actor: str, ip: str,
                    is_admin: bool = False, status: str = STATUS_ACTIVE,
                    phone: str = None, force_change: bool = True) -> int:
        """@brief 管理员建号(默认首登强改密,H03 §2 first_login_force_change)"""
        validate_password_policy(password, profile)
        phone_ct, phone_idx = None, None
        if phone:
            phone_ct = envelope_to_json(encrypt_envelope(
                phone.encode(), self._ring, self._suite))
            phone_idx = hmac_index(phone, self._ring.current_key, self._suite)
        self._db.execute(
            "INSERT INTO idp_users(account, display_name, password_hash, phone_ct,"
            " phone_index, status, is_admin, must_change_password,"
            " password_changed_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (account, display_name, hash_password(password, self._suite), phone_ct,
             phone_idx, status, 1 if is_admin else 0,
             1 if (force_change and profile.first_login_force_change) else 0,
             _now_iso(), _now_iso()))
        self._audit.append(actor, events.USER_CREATED, {"account": account}, ip)
        return self.get_user(account)["id"]

    def get_user(self, account: str) -> dict:
        """@brief 按账号取用户(不存在返回 None)"""
        rows = self._db.query(
            "SELECT id, account, display_name, password_hash, totp_secret_ct,"
            " status, is_admin, must_change_password, password_changed_at"
            " FROM idp_users WHERE account = ?", (account,))
        if not rows:
            return None
        keys = ("id", "account", "display_name", "password_hash", "totp_secret_ct",
                "status", "is_admin", "must_change_password", "password_changed_at")
        return dict(zip(keys, rows[0]))

    def user_groups(self, user_id: int) -> list:
        """@brief 用户所属组名列表(进 id_token groups 声明)"""
        rows = self._db.query(
            "SELECT g.name FROM idp_groups g JOIN idp_group_members m"
            " ON m.group_id = g.id WHERE m.user_id = ?", (user_id,))
        return [row[0] for row in rows]

    # ---- 锁定计数(Redis 跨实例,TTL 到期即自动解锁) ----------------------
    def _lock_key(self, account: str) -> str:
        """@brief 锁定标记键"""
        return make_key("idp", "lock", account)

    def is_locked(self, account: str) -> bool:
        """@brief 是否处于锁定期"""
        return self._store.get(self._lock_key(account)) is not None

    def record_failure(self, account: str, profile: SecurityProfile, ip: str):
        """@brief 记一次鉴别失败(口令或两步验证同计次,H04 §一.b)"""
        fail_key = make_key("idp", "fail", account)
        count = self._store.incr(fail_key, ttl_seconds=profile.lockout_minutes * 60)
        if count >= profile.max_login_failures:
            self._store.set(self._lock_key(account), "1",
                            ttl_seconds=profile.lockout_minutes * 60)
            self._audit.append(account, events.LOGIN_LOCKED,
                               {"failures": count,
                                "lockout_minutes": profile.lockout_minutes}, ip)

    def clear_failures(self, account: str):
        """@brief 登录成功清零计数"""
        self._store.delete(make_key("idp", "fail", account))

    def admin_unlock(self, account: str, actor: str, ip: str):
        """@brief 管理员立即解锁(只解锁不改密,留审计,06-E5)"""
        self._store.delete(self._lock_key(account))
        self._store.delete(make_key("idp", "fail", account))
        self._audit.append(actor, events.USER_UNLOCKED, {"account": account}, ip)

    # ---- 口令登录(第一步) ------------------------------------------------
    def password_login_step(self, account: str, password: str,
                            profile: SecurityProfile, ip: str) -> tuple:
        """
        @brief  口令校验步:返回 (结果码, 用户);登录错误统一措辞防枚举
        @return (LOGIN_*, user|None)
        """
        if not profile.method_password:
            return LOGIN_FAILED, None
        if self.is_locked(account):
            return LOGIN_LOCKED, None
        user = self.get_user(account)
        acceptable = {STATUS_ACTIVE, STATUS_DEMO} if profile.is_demo else {STATUS_ACTIVE}
        if user is None or user["status"] not in acceptable or not user["password_hash"]:
            if user is not None:
                self.record_failure(account, profile, ip)
            self._audit.append(account, events.LOGIN_FAILED, {"step": "password"}, ip)
            return LOGIN_FAILED, None
        is_ok, new_hash = verify_password(password, user["password_hash"], self._suite)
        if not is_ok:
            self.record_failure(account, profile, ip)
            self._audit.append(account, events.LOGIN_FAILED, {"step": "password"}, ip)
            return LOGIN_FAILED, None
        if new_hash:            # 透明重哈希为当前套件算法(H04 §8.2.5)
            self._db.execute("UPDATE idp_users SET password_hash = ? WHERE id = ?",
                             (new_hash, user["id"]))
        if user["totp_secret_ct"]:
            return LOGIN_NEED_TOTP, user
        if user["must_change_password"]:
            return LOGIN_MUST_CHANGE, user
        return LOGIN_OK, user

    def finish_login(self, user: dict, ip: str, method: str):
        """@brief 登录成功收尾:清计数、刷时间戳、审计"""
        self.clear_failures(user["account"])
        self._db.execute("UPDATE idp_users SET last_login_at = ? WHERE id = ?",
                         (_now_iso(), user["id"]))
        self._audit.append(user["account"], events.LOGIN_SUCCESS,
                           {"method": method}, ip)

    # ---- TOTP ------------------------------------------------------------
    def bind_totp(self, account: str, actor: str, ip: str) -> str:
        """@brief 生成并绑定 TOTP 密钥(信封加密落库),返回 base32 供二维码"""
        secret = totp_mod.generate_totp_secret()
        secret_ct = envelope_to_json(encrypt_envelope(
            secret.encode(), self._ring, self._suite))
        self._db.execute("UPDATE idp_users SET totp_secret_ct = ? WHERE account = ?",
                         (secret_ct, account))
        self._audit.append(actor, events.TWOFA_ENABLED, {"account": account}, ip)
        return secret

    def verify_totp_step(self, user: dict, code: str, profile: SecurityProfile,
                         ip: str) -> bool:
        """@brief 两步验证:D2 测试码仅经 profile 判定;失败与口令同计次"""
        if is_test_code_accepted(profile, code):
            return True
        if not user["totp_secret_ct"]:
            return False
        secret = decrypt_envelope(envelope_from_json(user["totp_secret_ct"]),
                                  self._ring).decode("ascii")
        if totp_mod.verify_totp(secret, code):
            return True
        self.record_failure(user["account"], profile, ip)
        self._audit.append(user["account"], events.LOGIN_FAILED, {"step": "totp"}, ip)
        return False

    # ---- 短信验证码(D3 回显仅经 profile) ---------------------------------
    def send_sms_code(self, account: str, profile: SecurityProfile) -> str:
        """@brief 生成验证码入 Redis(散列存储);DEMO 回显原码,生产返回 None"""
        code = "".join(secrets.choice("0123456789") for _ in range(SMS_CODE_DIGITS))
        digest = hmac_index(code, self._ring.current_key, self._suite)
        self._store.set(make_key("idp", "sms", account), digest,
                        ttl_seconds=SMS_CODE_TTL_SECONDS)
        return code if profile.sms_echo_enabled else None

    def verify_sms_code(self, account: str, code: str, profile: SecurityProfile,
                        ip: str) -> bool:
        """@brief 校验短信验证码(一次性,D2 测试码仅经 profile)"""
        if is_test_code_accepted(profile, code):
            return True
        record = self._store.get(make_key("idp", "sms", account))
        from gd_crypto import hmac_index_matches
        if record and hmac_index_matches(code, record, self._ring.current_key):
            self._store.delete(make_key("idp", "sms", account))
            return True
        self.record_failure(account, profile, ip)
        return False

    # ---- 改密与演示账号 ----------------------------------------------------
    def change_password(self, account: str, new_password: str,
                        profile: SecurityProfile, actor: str, ip: str):
        """@brief 改密(策略校验+清首登强改标记+审计)"""
        validate_password_policy(new_password, profile)
        self._db.execute(
            "UPDATE idp_users SET password_hash = ?, must_change_password = 0,"
            " password_changed_at = ? WHERE account = ?",
            (hash_password(new_password, self._suite), _now_iso(), account))
        self._audit.append(actor, events.PASSWORD_CHANGED, {"account": account}, ip)

    def seed_demo_accounts(self, profile: SecurityProfile, ip: str):
        """@brief D1:DEMO 启动播种演示账号(存在则重新启用)"""
        for account, display_name, is_admin in DEMO_SEED_ACCOUNTS:
            if self.get_user(account) is None:
                self.create_user(account, display_name, DEMO_SEED_PASSWORD, profile,
                                 "system", ip, is_admin=is_admin, status=STATUS_DEMO,
                                 force_change=False)
            else:
                self._db.execute("UPDATE idp_users SET status = ? WHERE account = ?",
                                 (STATUS_DEMO, account))

    def disable_demo_accounts(self, ip: str) -> int:
        """@brief 切生产:status=demo 账号自动停用(不删、留审计,H05 §3.2)"""
        rows = self._db.query(
            "SELECT account FROM idp_users WHERE status = ?", (STATUS_DEMO,))
        for (account,) in rows:
            self._db.execute("UPDATE idp_users SET status = ? WHERE account = ?",
                             (STATUS_DISABLED, account))
            self._audit.append("system", events.USER_DISABLED,
                               {"account": account, "reason": "demo→prod"}, ip)
        return len(rows)
