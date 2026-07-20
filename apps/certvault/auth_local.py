# -*- coding: utf-8 -*-
"""
@file    auth_local.py
@brief   certvault 本地口令登录(应急通道,H03 §7)+ 2FA(L02 §3 鉴权区):
         锁定 5 次/15 分钟(计数入易失态 gd:certvault:fail|lock 跨实例累加)、
         423「约 N 分钟后自动解锁」/401「再失败 N 次将锁定」、2FA 失败同计、
         改密即吊销全部旧令牌(token_valid_after)、90 天到期强改、
         口令 ≥10 位三类(H03 §2 统一提标)、TOTP 密钥信封加密存储。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time
from datetime import datetime, timezone

from gd_common.errors import PolicyValidationError
from gd_crypto import (
    decrypt_envelope, encrypt_envelope, envelope_from_json, envelope_to_json,
    hash_password, verify_password,
)
from gd_storage import make_key
from apps.idp.totp import generate_totp_secret, verify_totp
from apps.rp_common.accounts import STATUS_ACTIVE, UNUSABLE_PASSWORD_PREFIX

SYSTEM = "certvault"
LOCK_THRESHOLD = 5                    # H03 §2 max_login_failures
LOCK_MINUTES = 15                     # lockout_minutes
PASSWORD_MIN_LEN = 10                 # H03 §2:certvault 遗留 8 → 统一提到 10
PASSWORD_MAX_AGE_DAYS = 90
TOTP_AAD = b"cv_totp_secret"


def _now_iso() -> str:
    """@brief UTC ISO 时间"""
    return datetime.now(timezone.utc).isoformat()


def check_password_complexity(password: str):
    """@brief ≥10 位且大小写/数字/符号至少三类(H03 §2)"""
    if len(password or "") < PASSWORD_MIN_LEN:
        raise PolicyValidationError(f"口令长度须 ≥{PASSWORD_MIN_LEN} 位")
    classes = sum([any(ch.islower() for ch in password),
                   any(ch.isupper() for ch in password),
                   any(ch.isdigit() for ch in password),
                   any(not ch.isalnum() for ch in password)])
    if classes < 3:
        raise PolicyValidationError("口令须含大小写/数字/符号至少三类")


class LocalAuthService:
    """本地口令登录 + 锁定 + 2FA(易失态计数跨实例累加,09 §二 G.3)。"""

    def __init__(self, db, ring, suite, store, audit):
        """@brief 注入存储/密钥/易失态/审计"""
        self._db = db
        self._ring = ring
        self._suite = suite
        self._store = store
        self._audit = audit

    # ---- 锁定计数(gd:certvault:fail|lock:{account}) -------------------
    def _fail_key(self, account: str) -> str:
        return make_key(SYSTEM, "fail", account)

    def _lock_key(self, account: str) -> str:
        return make_key(SYSTEM, "lock", account)

    def is_locked(self, account: str) -> bool:
        """@brief 锁定中?(TTL 到期自动解锁)"""
        return self._store.get(self._lock_key(account)) is not None

    def _bump_failure(self, account: str, ip: str) -> int:
        """@brief 失败计数 +1;达阈值置锁定标记 @return 剩余可失败次数"""
        raw = self._store.get(self._fail_key(account))
        count = (int(raw) if raw else 0) + 1
        self._store.set(self._fail_key(account), str(count), LOCK_MINUTES * 60)
        if count >= LOCK_THRESHOLD:
            self._store.set(self._lock_key(account), _now_iso(),
                            LOCK_MINUTES * 60)
            self._audit.append(account, "login_locked",
                               {"system": SYSTEM, "failures": count}, ip)
        return max(LOCK_THRESHOLD - count, 0)

    def clear_failures(self, account: str):
        """@brief 登录成功/管理员解锁清零"""
        self._store.delete(self._fail_key(account))
        self._store.delete(self._lock_key(account))

    def locked_accounts(self) -> list:
        """@brief 管理页 /admin/locks:当前有锁定标记的账户"""
        rows = self._db.query("SELECT username FROM cv_users")
        return [row[0] for row in rows if self.is_locked(row[0])]

    # ---- 登录 -----------------------------------------------------------
    def login(self, username: str, password: str, totp_code: str,
              ip: str) -> dict:
        """
        @brief  口令登录(L02 文案契约)
        @return {user, totp_enabled, need_2fa_setup} 成功
        @raise  PolicyValidationError(带 http_status 属性 423/401/403)
        """
        if self.is_locked(username):
            raise _http_error(423,
                              f"账号已锁定,约 {LOCK_MINUTES} 分钟后自动解锁;"
                              "或联系管理员立即解锁")
        user = self._get_local_user(username)
        if user is None or not self._verify_password(user, password):
            remaining = self._bump_failure(username, ip)
            self._audit.append(username, "login_failed",
                               {"system": SYSTEM, "reason": "password"}, ip)
            if remaining == 0:
                raise _http_error(423,
                                  f"失败次数过多,账号已锁定 {LOCK_MINUTES} 分钟")
            raise _http_error(401, f"用户名或口令错误,再失败 {remaining} 次将锁定")
        if user["status"] != STATUS_ACTIVE:
            self._audit.append(username, "login_denied_disabled",
                               {"system": SYSTEM}, ip)
            raise _http_error(403, "账户已停用,禁止登录")
        if user["totp_enabled"]:
            if not totp_code or not verify_totp(
                    self._open_totp(user["totp_secret_ct"]), totp_code):
                remaining = self._bump_failure(username, ip)   # 2FA 失败同计
                self._audit.append(username, "login_failed",
                                   {"system": SYSTEM, "reason": "totp"}, ip)
                if remaining == 0:
                    raise _http_error(
                        423, f"失败次数过多,账号已锁定 {LOCK_MINUTES} 分钟")
                raise _http_error(
                    401, f"动态码缺失或错误,再失败 {remaining} 次将锁定")
        self.clear_failures(username)
        self._audit.append(username, "login_success", {"system": SYSTEM}, ip)
        return {"user": user, "totp_enabled": bool(user["totp_enabled"]),
                "need_2fa_setup": False}

    def _get_local_user(self, username: str) -> dict:
        """@brief 取含 2FA 列的本地账户"""
        rows = self._db.query(
            "SELECT id, username, display_name, role, sso_sub, status,"
            " password_hash, password_changed_at, totp_enabled,"
            " totp_secret_ct, totp_pending_ct, must_change_password"
            " FROM cv_users WHERE username = ?", (username,))
        if not rows:
            return None
        keys = ("id", "username", "display_name", "role", "sso_sub", "status",
                "password_hash", "password_changed_at", "totp_enabled",
                "totp_secret_ct", "totp_pending_ct", "must_change_password")
        return dict(zip(keys, rows[0]))

    def _verify_password(self, user: dict, password: str) -> bool:
        """@brief 口令核验(SSO 不可用口令直接拒 = 无口令旁路;支持透明重哈希)"""
        stored = user["password_hash"]
        if stored.startswith(UNUSABLE_PASSWORD_PREFIX):
            return False
        matched, new_hash = verify_password(password or "", stored, self._suite)
        if matched and new_hash:               # 套件升级透明重哈希(H04 §8.2.5)
            self._db.execute(
                "UPDATE cv_users SET password_hash = ? WHERE id = ?",
                (new_hash, user["id"]))
        return matched

    # ---- 口令生命周期 ---------------------------------------------------
    def password_expired(self, user: dict) -> bool:
        """@brief 90 天到期(SSO 用户由每次登录刷新时间戳而天然豁免,06-E16)"""
        if PASSWORD_MAX_AGE_DAYS <= 0:
            return False
        changed = datetime.fromisoformat(user["password_changed_at"])
        age_days = (datetime.now(timezone.utc) - changed).days
        return age_days >= PASSWORD_MAX_AGE_DAYS

    def change_password(self, username: str, old_password: str,
                        new_password: str, ip: str):
        """@brief 改密:复杂度校验 → 更新哈希与时间戳 → 吊销全部旧令牌"""
        user = self._get_local_user(username)
        if user is None or not self._verify_password(user, old_password):
            raise _http_error(401, "原口令错误")
        check_password_complexity(new_password)
        now = _now_iso()
        self._db.execute(
            "UPDATE cv_users SET password_hash = ?, password_changed_at = ?,"
            " must_change_password = 0, token_valid_after = ?"
            " WHERE username = ?",
            (hash_password(new_password, self._suite), now, now, username))
        self._audit.append(username, "password_changed", {"system": SYSTEM}, ip)

    # ---- 注册/建号 ------------------------------------------------------
    def register(self, username: str, password: str, display_name: str,
                 ip: str, force_role: str = None,
                 must_change: bool = False) -> dict:
        """@brief 建号(开放注册或管理员建号):首个账号自动 admin(L02)"""
        check_password_complexity(password)
        if self._get_local_user(username) is not None:
            raise _http_error(409, "用户名已存在")
        count = self._db.query("SELECT COUNT(*) FROM cv_users")[0][0]
        role = force_role or ("admin" if count == 0 else "user")
        now = _now_iso()
        self._db.execute(
            "INSERT INTO cv_users(username, display_name, password_hash, role,"
            " status, password_changed_at, token_valid_after,"
            " must_change_password, created_at)"
            " VALUES(?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (username, display_name or username,
             hash_password(password, self._suite), role, now, now,
             1 if must_change else 0, now))
        self._audit.append(username, "user_created",
                           {"system": SYSTEM, "role": role}, ip)
        return self._get_local_user(username)

    # ---- 2FA 三件套 -----------------------------------------------------
    def _seal_totp(self, secret: str) -> str:
        """@brief TOTP 密钥信封加密(L02:密钥信封加密存储)"""
        envelope = encrypt_envelope(secret.encode(), self._ring, self._suite,
                                    aad=TOTP_AAD)
        return envelope_to_json(envelope)

    def _open_totp(self, ciphertext: str) -> str:
        """@brief 解封 TOTP 密钥"""
        return decrypt_envelope(envelope_from_json(ciphertext), self._ring,
                                aad=TOTP_AAD).decode()

    def setup_2fa(self, username: str) -> str:
        """@brief 生成 secret 存 totp_pending,返回 otpauth URI 供扫码"""
        secret = generate_totp_secret()
        self._db.execute(
            "UPDATE cv_users SET totp_pending_ct = ? WHERE username = ?",
            (self._seal_totp(secret), username))
        return (f"otpauth://totp/certvault:{username}?secret={secret}"
                f"&issuer=gangdian-certvault")

    def enable_2fa(self, username: str, code: str, ip: str):
        """@brief 验证 6 位码后正式启用"""
        user = self._get_local_user(username)
        if not user or not user["totp_pending_ct"]:
            raise _http_error(400, "请先执行 2FA setup")
        if not verify_totp(self._open_totp(user["totp_pending_ct"]), code):
            raise _http_error(401, "动态码错误,未启用")
        self._db.execute(
            "UPDATE cv_users SET totp_enabled = 1, totp_secret_ct ="
            " totp_pending_ct, totp_pending_ct = '' WHERE username = ?",
            (username,))
        self._audit.append(username, "twofa_enabled", {"system": SYSTEM}, ip)

    def disable_2fa(self, username: str, ip: str):
        """@brief 关闭两步验证"""
        self._db.execute(
            "UPDATE cv_users SET totp_enabled = 0, totp_secret_ct = '',"
            " totp_pending_ct = '' WHERE username = ?", (username,))
        self._audit.append(username, "twofa_disabled", {"system": SYSTEM}, ip)

    def reset_2fa(self, username: str, actor: str, ip: str):
        """@brief 管理员重置 2FA(用户丢失设备自救,L02 admin 区)"""
        self.disable_2fa(username, ip)
        self._audit.append(actor, "twofa_reset",
                           {"system": SYSTEM, "target": username}, ip)


def _http_error(status: int, message: str) -> PolicyValidationError:
    """@brief 带 HTTP 状态的策略异常(路由层读 http_status)"""
    error = PolicyValidationError(message)
    error.http_status = status
    return error
