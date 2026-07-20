# -*- coding: utf-8 -*-
"""
@file    base.py
@brief   测试公共基座:路径注入、临时数据库/密钥环构造
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from gd_crypto import MasterKeyRing                 # noqa: E402
from gd_storage import apply_migrations, Database   # noqa: E402

TEST_KEY_HEX = "a1" * 32          # 测试密钥(仅测试内使用的确定性值)
TEST_OLD_KEY_HEX = "b2" * 32


def make_temp_db() -> Database:
    """@brief 创建带全量迁移的临时 SQLite 库"""
    path = tempfile.mktemp(suffix=".db")
    db = Database(f"sqlite:///{path}")
    apply_migrations(db)
    return db


def make_ring() -> MasterKeyRing:
    """@brief 构造测试主密钥环(当前钥 mk1)"""
    return MasterKeyRing({"mk1": bytes.fromhex(TEST_KEY_HEX)}, "mk1")
