# -*- coding: utf-8 -*-
"""
@file    test_policy_profile.py
@brief   策略服务与 SecurityProfile 回归:四层优先级(L2-19)、未知键 fail-fast、
         null=删除覆盖、生产等保下限钳制(G3)、DEMO 环境检查、模式单一入口静态检查、
         套件与模式正交
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import tempfile
import unittest

from tests.base import make_temp_db, REPO_ROOT

from gd_common.errors import ConfigError, PolicyValidationError
from gd_crypto import get_suite, SUITE_INTL
from gd_policy import check_bind_allowed, resolve_profile, SettingsService
from gd_storage import AuditWriter
from scripts.check_demo_mode_usage import scan as scan_demo_mode_usage

SAMPLE_IP = "10.0.0.9"


def _make_settings(db, config_yaml: str = None, environ: dict = None) -> SettingsService:
    """@brief 构造设置服务(可注入配置文件与环境)"""
    config_path = None
    if config_yaml is not None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                             encoding="utf-8")
        handle.write(config_yaml)
        handle.close()
        config_path = handle.name
    return SettingsService(db, config_file=config_path, environ=environ or {})


class TestSettingsService(unittest.TestCase):
    """统一设置服务(H03 §8 / 02-C3)。"""

    def setUp(self):
        self.db = make_temp_db()

    def tearDown(self):
        self.db.close()

    def test_l219_priority_env_override_file_default(self):
        """优先级:环境变量 > 管理后台 > 配置文件 > 默认值(H00 G2)"""
        settings = _make_settings(self.db, "lockout_minutes: 20\n",
                                  environ={"LOCKOUT_MINUTES": "25"})
        self.assertEqual(settings.get_with_source("lockout_minutes"), (25, "env"))
        settings_no_env = _make_settings(self.db, "lockout_minutes: 20\n")
        settings_no_env.set_override("lockout_minutes", 30, "admin", SAMPLE_IP)
        self.assertEqual(settings_no_env.get_with_source("lockout_minutes"),
                         (30, "override"))
        settings_no_env.set_override("lockout_minutes", None, "admin", SAMPLE_IP)
        self.assertEqual(settings_no_env.get_with_source("lockout_minutes"), (20, "file"))
        self.assertEqual(_make_settings(self.db).get_with_source("lockout_minutes"),
                         (15, "default"))

    def test_unknown_config_key_fails_startup(self):
        """配置文件未知键启动报错,防拼写错误(02-C3)"""
        with self.assertRaises(ConfigError):
            _make_settings(self.db, "lockout_minutess: 15\n")

    def test_range_validation_rejected(self):
        """范围校验:越界值在任何来源层都被拒绝(L2-19)"""
        with self.assertRaises(ConfigError):
            _make_settings(self.db, "max_login_failures: 99\n")
        settings = _make_settings(self.db)
        with self.assertRaises(ConfigError):
            settings.set_override("password_min_length", 4, "admin", SAMPLE_IP)

    def test_env_locked_param_rejects_override(self):
        """env 已锁定的参数拒绝后台修改并明示原因(H03 §8)"""
        settings = _make_settings(self.db, environ={"LOCKOUT_MINUTES": "25"})
        with self.assertRaises(PolicyValidationError):
            settings.set_override("lockout_minutes", 30, "admin", SAMPLE_IP)

    def test_settings_change_audited_and_version_bumped(self):
        """修改留审计(谁/哪项/旧值→新值)且设置版本号递增(H12 §五)"""
        settings = _make_settings(self.db)
        writer = AuditWriter(self.db, get_suite(SUITE_INTL))
        self.assertEqual(settings.version(), 0)
        settings.set_override("session_idle_minutes", 20, "admin", SAMPLE_IP,
                              audit_writer=writer)
        self.assertEqual(settings.version(), 1)
        rows = self.db.query(
            "SELECT actor, detail FROM audit_logs WHERE action = 'settings_changed'")
        self.assertEqual(len(rows), 1)
        self.assertIn("session_idle_minutes", rows[0][1])


class TestSecurityProfile(unittest.TestCase):
    """SecurityProfile:唯一模式入口与等保钳制(H05 §1 / H00 G3)。"""

    def setUp(self):
        self.db = make_temp_db()

    def tearDown(self):
        self.db.close()

    def test_g3_prod_floor_clamped(self):
        """生产模式对低于等保下限的显式配置强制钳制(H03 ai_directives)"""
        settings = _make_settings(
            self.db,
            "max_login_failures: 10\nlockout_minutes: 5\npassword_max_age_days: 0\n")
        profile = resolve_profile(settings, environ={})
        self.assertEqual(profile.mode, "prod")            # 不配置即生产(H05 §1.1)
        self.assertEqual(profile.max_login_failures, 5)   # ≤5
        self.assertEqual(profile.lockout_minutes, 15)     # ≥15
        self.assertEqual(profile.password_max_age_days, 90)   # 禁 0

    def test_demo_keeps_configured_values(self):
        """DEMO 模式不钳制(仅 H05 清单内简化生效)"""
        settings = _make_settings(self.db, "lockout_minutes: 5\n",
                                  environ={"DEMO_MODE": "1"})
        profile = resolve_profile(settings, environ={"DEMO_MODE": "1"})
        self.assertTrue(profile.is_demo)
        self.assertEqual(profile.lockout_minutes, 5)
        self.assertEqual(profile.rate_multiplier, 10)     # D8

    def test_demo_exposed_bind_blocked(self):
        """0.0.0.0 + DEMO 启动被阻止;显式 DEMO_ALLOW_EXPOSED=1 放行(H05 §2)"""
        settings = _make_settings(self.db, environ={"DEMO_MODE": "1"})
        profile = resolve_profile(settings, environ={"DEMO_MODE": "1"})
        with self.assertRaises(ConfigError):
            check_bind_allowed("0.0.0.0", profile, environ={})
        check_bind_allowed("0.0.0.0", profile, environ={"DEMO_ALLOW_EXPOSED": "1"})
        check_bind_allowed("127.0.0.1", profile, environ={})

    def test_demo_mode_single_source_static_check(self):
        """业务代码禁读 DEMO_MODE 静态检查零违规(H09 E 组 / H05 ai_directives)"""
        self.assertEqual(scan_demo_mode_usage(REPO_ROOT), [])

    def test_crypto_suite_orthogonal_to_demo(self):
        """套件与模式正交:DEMO 不降级算法,套件仅由 CRYPTO_SUITE 决定(H05 §2)"""
        settings = _make_settings(self.db, environ={"DEMO_MODE": "1"})
        demo_env = {"DEMO_MODE": "1", "CRYPTO_SUITE": "intl"}
        profile = resolve_profile(settings, environ=demo_env)
        self.assertEqual(profile.crypto_suite_name, "intl")
        prod_profile = resolve_profile(_make_settings(self.db), environ={})
        self.assertEqual(prod_profile.crypto_suite_name, "intl")   # 默认即国际套件(F.5)


if __name__ == "__main__":
    unittest.main()
