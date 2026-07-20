# -*- coding: utf-8 -*-
"""
@file    profile.py
@brief   SecurityProfile 解析器(H05 §1.2):全平台唯一的运行模式判断入口。
         启动/热切换时产出一份不可变生效策略快照;业务代码只读快照,
         MUST NOT 直接判 DEMO_MODE(CI 静态检查白名单仅本文件与启动检查)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import dataclasses
import os

from gd_common.errors import ConfigError
from gd_common.jsonlog import get_logger
from gd_crypto.suites import ENV_CRYPTO_SUITE, SUITE_INTL
from gd_policy.schema import (
    FLOOR_AUDIT_RETENTION_DAYS,
    FLOOR_LOCKOUT_MINUTES,
    FLOOR_MAX_LOGIN_FAILURES,
    FLOOR_PASSWORD_MAX_AGE_DAYS,
    AUDIT_RETENTION_PERMANENT,
    SCHEMA_BY_KEY,
)
from gd_policy.service import SettingsService

_logger = get_logger("gd_policy.profile")

MODE_DEMO = "demo"
MODE_PROD = "prod"
TEST_TOTP_SMS_CODE = "123456"       # DEMO 预设测试码(05-D2);生产同一校验必须失败
DEMO_RATE_MULTIPLIER = 10           # DEMO 限速阈值倍数(05-D8)
PROD_RATE_MULTIPLIER = 1
BANNER_DEMO = "演示模式,仅限测试"    # 全站红色横幅文案(05-D9)
BANNER_PROD = "生产模式"
ENV_DEMO_ALLOW_EXPOSED = "DEMO_ALLOW_EXPOSED"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


@dataclasses.dataclass(frozen=True)
class SecurityProfile:
    """不可变生效策略快照:业务代码的唯一模式与策略读取面。"""

    mode: str
    is_demo: bool
    crypto_suite_name: str            # 与模式正交:DEMO 不改变套件(H05 §2)
    banner_text: str
    # ---- DEMO 简化清单 D1–D9 的生效位 ----
    seed_accounts_enabled: bool       # D1 种子演示账号
    test_code_enabled: bool           # D2 测试验证码
    sms_echo_enabled: bool            # D3 短信验证码回显
    cert_demo_endpoint_enabled: bool  # D4 证书测试入口
    wechat_mock_enabled: bool         # D5 微信测试扫码
    cookie_secure_required: bool      # D6 生产强制 Secure Cookie
    enforce_password_age: bool        # D7 口令 90 天策略(DEMO 不强制)
    rate_multiplier: int              # D8 限速倍数
    # ---- 钳制后的等保参数(生产按下限强制钳制,H03 ai_directives) ----
    password_min_length: int
    password_complexity: str
    password_max_age_days: int
    first_login_force_change: bool
    password_history: int
    max_login_failures: int
    lockout_minutes: int
    login_rate_cap_per_minute: int
    session_idle_minutes: int
    session_max_hours: int
    audit_retention_days: int
    # ---- 登录方式开关(每方式独立可控、保存即热生效,H03 §4) ----
    method_password: bool
    method_totp_only: bool
    method_sms: bool
    method_client_cert: bool
    method_wechat: bool
    settings_version: int


def _clamp_prod(key: str, value):
    """
    @brief  生产模式等保下限强制钳制:显式配置低于下限时钳到下限并告警
            (默认值即等保态,配置不当不得导致"变不安全",H00 G3)
    @return 钳制后的值
    """
    clamped = value
    if key == "max_login_failures" and value > FLOOR_MAX_LOGIN_FAILURES:
        clamped = FLOOR_MAX_LOGIN_FAILURES
    elif key == "lockout_minutes" and value < FLOOR_LOCKOUT_MINUTES:
        clamped = FLOOR_LOCKOUT_MINUTES
    elif key == "password_max_age_days" and (
            value == 0 or value > FLOOR_PASSWORD_MAX_AGE_DAYS):
        clamped = FLOOR_PASSWORD_MAX_AGE_DAYS
    elif key == "first_login_force_change" and not value:
        clamped = True
    elif key == "audit_retention_days" and (
            value != AUDIT_RETENTION_PERMANENT and value < FLOOR_AUDIT_RETENTION_DAYS):
        clamped = FLOOR_AUDIT_RETENTION_DAYS
    if clamped != value:
        _logger.warning("生产模式等保下限钳制生效",
                        extra={"ctx": {"key": key, "configured": value, "clamped": clamped}})
    return clamped


def resolve_profile(settings: SettingsService, environ: dict = None) -> SecurityProfile:
    """
    @brief  解析生效策略快照。demo_mode 经设置服务读取(env > 后台覆盖 > 默认 0=生产,
            遗留默认 1 已按 H05 §1.1 反转);本函数是 DEMO 判定的唯一入口。
    @param  settings 统一设置服务
    @param  environ  环境字典(测试注入)
    @return SecurityProfile 不可变快照
    """
    env = os.environ if environ is None else environ
    is_demo = bool(settings.get("demo_mode"))
    mode = MODE_DEMO if is_demo else MODE_PROD

    def _policy(key: str):
        value = settings.get(key)
        return value if is_demo else _clamp_prod(key, value)

    return SecurityProfile(
        mode=mode,
        is_demo=is_demo,
        crypto_suite_name=env.get(ENV_CRYPTO_SUITE, SUITE_INTL),
        banner_text=BANNER_DEMO if is_demo else BANNER_PROD,
        seed_accounts_enabled=is_demo,
        test_code_enabled=is_demo,
        sms_echo_enabled=is_demo,
        cert_demo_endpoint_enabled=is_demo,
        wechat_mock_enabled=is_demo,
        cookie_secure_required=not is_demo,
        enforce_password_age=not is_demo,
        rate_multiplier=DEMO_RATE_MULTIPLIER if is_demo else PROD_RATE_MULTIPLIER,
        password_min_length=_policy("password_min_length"),
        password_complexity=settings.get("password_complexity"),
        password_max_age_days=_policy("password_max_age_days"),
        first_login_force_change=_policy("first_login_force_change"),
        password_history=settings.get("password_history"),
        max_login_failures=_policy("max_login_failures"),
        lockout_minutes=_policy("lockout_minutes"),
        login_rate_cap_per_minute=settings.get("login_rate_cap_per_minute"),
        session_idle_minutes=settings.get("session_idle_minutes"),
        session_max_hours=settings.get("session_max_hours"),
        audit_retention_days=_policy("audit_retention_days"),
        method_password=settings.get("method_password"),
        method_totp_only=settings.get("method_totp_only"),
        method_sms=settings.get("method_sms"),
        method_client_cert=settings.get("method_client_cert"),
        method_wechat=settings.get("method_wechat"),
        settings_version=settings.version(),
    )


def is_test_code_accepted(profile: SecurityProfile, code: str) -> bool:
    """
    @brief  测试码判定(05-D2 成对断言的被测函数):
            DEMO 且 code==预设测试码 → True;生产对同一测试码 MUST 返回 False
    """
    return profile.test_code_enabled and code == TEST_TOTP_SMS_CODE


def check_bind_allowed(bind_host: str, profile: SecurityProfile, environ: dict = None):
    """
    @brief  DEMO 环境检查(H05 §2):绑定非回环地址且 DEMO=1 时阻止启动,
            除非显式 DEMO_ALLOW_EXPOSED=1(启动日志大写提示 + 审计由调用方补记)
    @raise  ConfigError 阻止启动
    """
    env = os.environ if environ is None else environ
    if not profile.is_demo or bind_host in LOOPBACK_HOSTS:
        return
    if env.get(ENV_DEMO_ALLOW_EXPOSED) == "1":
        _logger.warning("警告:DEMO 模式绑定非回环地址已被显式放行(DEMO_ALLOW_EXPOSED=1),"
                        "仅限隔离测试网使用")
        return
    raise ConfigError(
        f"DEMO 模式禁止绑定非回环地址 {bind_host}(H05 §2)。"
        f"如确在隔离测试网,显式设置 {ENV_DEMO_ALLOW_EXPOSED}=1 放行。"
    )


def describe_schema_floor(key: str) -> str:
    """@brief 设置页等保下限提示文案(H03 §8)"""
    param = SCHEMA_BY_KEY.get(key)
    return param.help if param else ""
