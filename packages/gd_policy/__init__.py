# -*- coding: utf-8 -*-
"""
@file    __init__.py
@brief   gd_policy:统一策略中心共享库(H03 schema 单一来源 + 设置服务 + SecurityProfile)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_policy.profile import (
    check_bind_allowed,
    is_test_code_accepted,
    resolve_profile,
    SecurityProfile,
)
from gd_policy.schema import SCHEMA_BY_KEY, SETTINGS_SCHEMA
from gd_policy.service import SettingsService

__all__ = ["SETTINGS_SCHEMA", "SCHEMA_BY_KEY", "SettingsService", "SecurityProfile",
           "resolve_profile", "is_test_code_accepted", "check_bind_allowed"]
