# -*- coding: utf-8 -*-
"""
@file    __init__.py
@brief   gd_storage:存储层共享库(Database 抽象/审计链/迁移/易失态存储,H12)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_storage.audit import AuditWriter, verify_chain
from gd_storage.database import Database
from gd_storage.migrations import apply_migrations
from gd_storage.volatile import (
    IVolatileStore,
    LocalVolatileStore,
    make_key,
    RedisVolatileStore,
)

__all__ = ["Database", "apply_migrations", "AuditWriter", "verify_chain",
           "IVolatileStore", "LocalVolatileStore", "RedisVolatileStore", "make_key"]
