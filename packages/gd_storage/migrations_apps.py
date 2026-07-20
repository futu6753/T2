# -*- coding: utf-8 -*-
"""
@file    migrations_apps.py
@brief   业务子系统迁移分册(v7 factory-3d / v8 quiz),由 migrations.py
         聚合执行;拆分仅为遵守单文件 ≤500 行工程红线(H01 §三),
         版本序与执行语义不变。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""

APP_MIGRATIONS = [
    {
        # 里程碑 6:factory-3d 业务表(L03 §6/§7 / H02-D)。布局单例整体 JSON
        # 存储(结构变更 data_rev+1);每设备至多一条在途告警由唯一索引兜底;
        # 助手事务日志有界留存;外部注入密钥密文落库(明文仅创建时一次)。
        "version": 7,
        "name": "f3d_business_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS f3d_layout ("
            " id INTEGER PRIMARY KEY CHECK (id = 1),"
            " doc TEXT NOT NULL,"
            " data_rev INTEGER NOT NULL DEFAULT 0,"
            " updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS f3d_alarms ("
            " id INTEGER PRIMARY KEY,"
            " device_id TEXT NOT NULL,"
            " state TEXT NOT NULL"
            "  CHECK (state IN ('pending','active','acked')),"
            " started_at TEXT NOT NULL,"
            " activated_at TEXT,"
            " acked_at TEXT)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_f3d_alarms_device"
            " ON f3d_alarms(device_id)",
            "CREATE TABLE IF NOT EXISTS f3d_alarm_history ("
            " id INTEGER PRIMARY KEY,"
            " device_id TEXT NOT NULL,"
            " outcome TEXT NOT NULL CHECK (outcome IN ('silent','cleared')),"
            " reached TEXT NOT NULL,"
            " started_at TEXT NOT NULL,"
            " ended_at TEXT NOT NULL,"
            " duration_seconds INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS f3d_events ("
            " id INTEGER PRIMARY KEY,"
            " ts TEXT NOT NULL,"
            " kind TEXT NOT NULL,"
            " device_id TEXT NOT NULL DEFAULT '',"
            " building TEXT NOT NULL DEFAULT '',"
            " from_status TEXT NOT NULL DEFAULT '',"
            " to_status TEXT NOT NULL DEFAULT '',"
            " detail TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS idx_f3d_events_ts ON f3d_events(ts)",
            "CREATE TABLE IF NOT EXISTS f3d_models ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " filename TEXT NOT NULL,"
            " size_bytes INTEGER NOT NULL DEFAULT 0,"
            " uploaded_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS f3d_tx_log ("
            " id INTEGER PRIMARY KEY,"
            " ts TEXT NOT NULL,"
            " scope TEXT NOT NULL,"
            " operator TEXT NOT NULL,"
            " phase TEXT NOT NULL CHECK (phase IN"
            "  ('dry_run','executed','rolled_back','rejected')),"
            " actions_json TEXT NOT NULL,"
            " result_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS f3d_external_keys ("
            " id INTEGER PRIMARY KEY,"
            " key_id TEXT NOT NULL UNIQUE,"
            " secret_ct TEXT NOT NULL,"
            " enabled INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT NOT NULL,"
            " revoked_at TEXT)",
        ],
    },
    {
        # 里程碑 7:安全刷题业务表(H02-E / H03 §6)。owner 统一为
        # "sso:<用户名>" 或 "guest:<5位ID>";SRS ease 与 ELO 评分整数化存储
        # (H07 L1-06 禁浮点相等);迁移码只存散列(13-R-QZ-3)。
        "version": 8,
        "name": "quiz_business_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS quiz_questions ("
            " id INTEGER PRIMARY KEY,"
            " qno INTEGER NOT NULL UNIQUE,"
            " qtype TEXT NOT NULL CHECK (qtype IN"
            "  ('single','multi','judge','risk','image')),"
            " color TEXT NOT NULL CHECK (color IN"
            "  ('none','yellow','cyan','green')),"
            " stem TEXT NOT NULL,"
            " options_json TEXT NOT NULL DEFAULT '[]',"
            " answer TEXT NOT NULL,"
            " analysis TEXT NOT NULL DEFAULT '',"
            " image TEXT NOT NULL DEFAULT '',"
            " difficulty INTEGER NOT NULL DEFAULT 1200)",
            "CREATE TABLE IF NOT EXISTS quiz_progress ("
            " id INTEGER PRIMARY KEY,"
            " owner TEXT NOT NULL,"
            " question_id INTEGER NOT NULL,"
            " correct_count INTEGER NOT NULL DEFAULT 0,"
            " wrong_count INTEGER NOT NULL DEFAULT 0,"
            " in_wrongbook INTEGER NOT NULL DEFAULT 0,"
            " last_result TEXT NOT NULL DEFAULT '',"
            " updated_at TEXT NOT NULL)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_progress_owner_q"
            " ON quiz_progress(owner, question_id)",
            "CREATE TABLE IF NOT EXISTS quiz_srs ("
            " id INTEGER PRIMARY KEY,"
            " owner TEXT NOT NULL,"
            " question_id INTEGER NOT NULL,"
            " ease_x100 INTEGER NOT NULL DEFAULT 250,"
            " interval_days INTEGER NOT NULL DEFAULT 0,"
            " reps INTEGER NOT NULL DEFAULT 0,"
            " lapses INTEGER NOT NULL DEFAULT 0,"
            " due_at TEXT NOT NULL)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_srs_owner_q"
            " ON quiz_srs(owner, question_id)",
            "CREATE INDEX IF NOT EXISTS idx_quiz_srs_due ON quiz_srs(due_at)",
            "CREATE TABLE IF NOT EXISTS quiz_ability ("
            " id INTEGER PRIMARY KEY,"
            " owner TEXT NOT NULL UNIQUE,"
            " rating INTEGER NOT NULL DEFAULT 1200,"
            " games INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS quiz_prefs ("
            " id INTEGER PRIMARY KEY,"
            " owner TEXT NOT NULL UNIQUE,"
            " elo_sampling INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS quiz_migrate_codes ("
            " id INTEGER PRIMARY KEY,"
            " code_hash TEXT NOT NULL UNIQUE,"
            " guest_code TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " expires_at TEXT NOT NULL,"
            " used_at TEXT)",
        ],
    },
    {
        # 里程碑 10:平台元状态表(套件切换检测,H04 §8.2.8 / F.5)。
        # key-value 形态,当前仅 crypto_suite 一键;expand 型追加不触碰既有表。
        "version": 9,
        "name": "platform_meta_table",
        "common": [
            "CREATE TABLE IF NOT EXISTS platform_meta ("
            " key TEXT PRIMARY KEY,"
            " value TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)",
        ],
    },
]
