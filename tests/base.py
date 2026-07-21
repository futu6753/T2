# -*- coding: utf-8 -*-
"""
@file    base.py
@brief   测试公共基座:路径注入、临时数据库/密钥环构造
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import atexit
import secrets
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
    """@brief 创建带全量迁移的临时库(默认 SQLite;GD_DB_URL 时 PG,J.1)"""
    db = Database(make_db_url())
    apply_migrations(db)
    return db


def make_ring() -> MasterKeyRing:
    """@brief 构造测试主密钥环(当前钥 mk1)"""
    return MasterKeyRing({"mk1": bytes.fromhex(TEST_KEY_HEX)}, "mk1")


_PG_CREATED_DBS: list = []          # 本进程创建的临时库(atexit 统一回收)


def _drop_created_pg_dbs():
    """@brief 进程退出时回收全部临时 PG 库(WITH FORCE 兜住未关连接;
    防测试库堆积撑爆磁盘——2026-07-21 双库同测实测教训)"""
    base = os.environ.get("GD_DB_URL", "") or _PG_CREATED_DBS and _PG_CREATED_DBS[0][0]
    if not _PG_CREATED_DBS:
        return
    import psycopg
    try:
        admin = psycopg.connect(_PG_CREATED_DBS[0][0], autocommit=True)
    except Exception:                        # noqa: BLE001 清理尽力而为
        return
    for admin_url, name in _PG_CREATED_DBS:
        try:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        except Exception:                    # noqa: BLE001
            pass
    admin.close()


atexit.register(_drop_created_pg_dbs)


def make_db_url() -> str:
    """
    @brief  测试库 URL 工厂(H09-J.1 双库同测入口):
            未设 GD_DB_URL → SQLite 临时文件(单机默认);
            设 GD_DB_URL=postgresql://... → 以管理连接为本环境创建唯一
            独立库(隔离并发测试环境),返回指向该库的 URL;
            进程退出时 atexit 统一 DROP 回收。
    """
    base = os.environ.get("GD_DB_URL", "")
    if not base:
        return f"sqlite:///{tempfile.mktemp(suffix='.db')}"
    import psycopg
    name = "gd_t_" + secrets.token_hex(6)
    admin = psycopg.connect(base, autocommit=True)
    try:
        admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()
    _PG_CREATED_DBS.append((base, name))
    return base.rsplit("/", 1)[0] + "/" + name
