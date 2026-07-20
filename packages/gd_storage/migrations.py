# -*- coding: utf-8 -*-
"""
@file    migrations.py
@brief   迁移基线(H12 §六):编号 SQL、双方言、线性版本、schema_migrations 台账;
         幂等且向后兼容(expand 型);迁移脚本禁止 import 业务模型代码。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_storage.database import Database, DIALECT_SQLITE

# 审计表禁改触发器:PG 与 SQLite 各自方言实现(H12 §四)
_SQLITE_AUDIT_TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, '审计记录只增不改:禁止 UPDATE'); END",
    "CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, '审计记录只增不改:禁止 DELETE'); END",
]
_POSTGRES_AUDIT_TRIGGERS = [
    "CREATE OR REPLACE FUNCTION audit_immutable() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '审计记录只增不改:禁止 % 操作', TG_OP; END; "
    "$$ LANGUAGE plpgsql",
    "DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_logs",
    "CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION audit_immutable()",
]

# 迁移清单:线性递增版本号;新表/新列一律 expand 型追加,MUST NOT 触碰既有列语义
MIGRATIONS = [
    {
        "version": 1,
        "name": "platform_base_tables",
        "common": [
            # 迁移台账(H12 §六.1)
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)",
            # 设置覆盖表(H03 §8 / 02-C3:管理后台层持久化)
            "CREATE TABLE IF NOT EXISTS settings_overrides ("
            " id INTEGER PRIMARY KEY,"
            " key TEXT NOT NULL UNIQUE,"
            " value TEXT,"
            " updated_at TEXT NOT NULL)",
            # 审计表(H12 §四:只增不改+链式哈希,逐条记录 alg)
            "CREATE TABLE IF NOT EXISTS audit_logs ("
            " id INTEGER PRIMARY KEY,"
            " ts TEXT NOT NULL,"
            " actor TEXT NOT NULL,"
            " action TEXT NOT NULL,"
            " detail TEXT NOT NULL,"
            " ip TEXT NOT NULL,"
            " alg TEXT NOT NULL,"
            " prev_hash TEXT NOT NULL,"
            " hash TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts)",
        ],
        "sqlite": _SQLITE_AUDIT_TRIGGERS,
        "postgres": _POSTGRES_AUDIT_TRIGGERS,
    },
    {
        # IdP 统一用户目录(H08 §1;ARC-1 唯一主源)。
        # 说明:失败计数/锁定不建列——共享易失态一律入 Redis(H06-E13/ARC-4),
        # 锁定 TTL 到期自动解锁即等保"到期自动解"语义;会话同理不落库。
        "version": 2,
        "name": "idp_directory_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS idp_users ("
            " id INTEGER PRIMARY KEY,"
            " account TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL,"
            " password_hash TEXT,"
            " phone_ct TEXT,"
            " phone_index TEXT,"
            " totp_secret_ct TEXT,"
            " cert_fingerprints TEXT NOT NULL DEFAULT '[]',"
            " wechat_openid_hash TEXT,"
            " status TEXT NOT NULL DEFAULT 'active',"
            " is_admin INTEGER NOT NULL DEFAULT 0,"
            " must_change_password INTEGER NOT NULL DEFAULT 0,"
            " password_changed_at TEXT,"
            " last_login_at TEXT,"
            " created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_idp_users_phone ON idp_users(phone_index)",
            "CREATE TABLE IF NOT EXISTS idp_groups ("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
            "CREATE TABLE IF NOT EXISTS idp_group_members ("
            " group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,"
            " UNIQUE(group_id, user_id))",
            "CREATE TABLE IF NOT EXISTS idp_clients ("
            " id INTEGER PRIMARY KEY,"
            " client_id TEXT NOT NULL UNIQUE,"
            " secret_hash TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " redirect_uris TEXT NOT NULL DEFAULT '[]',"
            " backchannel_url TEXT,"
            " post_logout_uris TEXT NOT NULL DEFAULT '[]',"
            " access_policy TEXT NOT NULL DEFAULT 'all',"
            " access_groups TEXT NOT NULL DEFAULT '[]',"
            " enabled INTEGER NOT NULL DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS idp_links ("
            " id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,"
            " groups TEXT NOT NULL DEFAULT '[]')",
            "CREATE TABLE IF NOT EXISTS idp_consents ("
            " id INTEGER PRIMARY KEY, account TEXT NOT NULL,"
            " privacy_version TEXT NOT NULL, ts TEXT NOT NULL)",
        ],
    },
    {
        # 里程碑 3:RP 本地账户表(H03 §1 各系统角色子集;SSO 自动建号映射)。
        # 统一列:sso_sub 唯一映射(重复登录固定映射,09 §二 C.3)、
        # password_changed_at 每次 SSO 登录刷新(06-E16)、token_valid_after
        # 仅 certvault 用(JWT iat 吊销检查,H03 §6)。
        "version": 3,
        "name": "rp_local_account_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS cv_users ("
            " id INTEGER PRIMARY KEY,"
            " username TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL,"
            " password_hash TEXT NOT NULL,"
            " role TEXT NOT NULL DEFAULT 'user'"
            "  CHECK (role IN ('admin', 'user')),"
            " sso_sub TEXT UNIQUE,"
            " status TEXT NOT NULL DEFAULT 'active'"
            "  CHECK (status IN ('active', 'disabled', 'demo')),"
            " password_changed_at TEXT NOT NULL,"
            " token_valid_after TEXT NOT NULL,"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS nvr_users ("
            " id INTEGER PRIMARY KEY,"
            " username TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL,"
            " password_hash TEXT NOT NULL,"
            " role TEXT NOT NULL DEFAULT 'auditor'"
            "  CHECK (role IN ('admin', 'operator', 'auditor')),"
            " sso_sub TEXT UNIQUE,"
            " status TEXT NOT NULL DEFAULT 'active'"
            "  CHECK (status IN ('active', 'disabled', 'demo')),"
            " password_changed_at TEXT NOT NULL,"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS f3d_users ("
            " id INTEGER PRIMARY KEY,"
            " username TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL,"
            " password_hash TEXT NOT NULL,"
            " role TEXT NOT NULL DEFAULT 'operator'"
            "  CHECK (role IN ('admin', 'operator')),"
            " sso_sub TEXT UNIQUE,"
            " status TEXT NOT NULL DEFAULT 'active'"
            "  CHECK (status IN ('active', 'disabled', 'demo')),"
            " password_changed_at TEXT NOT NULL,"
            " created_at TEXT NOT NULL)",
            # quiz:SSO 账户与游客并存(H03 §6);游客仅 5 位数字码,不涉个人信息
            "CREATE TABLE IF NOT EXISTS quiz_users ("
            " id INTEGER PRIMARY KEY,"
            " username TEXT NOT NULL UNIQUE,"
            " display_name TEXT NOT NULL,"
            " role TEXT NOT NULL DEFAULT 'user'"
            "  CHECK (role IN ('admin', 'user')),"
            " sso_sub TEXT UNIQUE,"
            " status TEXT NOT NULL DEFAULT 'active'"
            "  CHECK (status IN ('active', 'disabled', 'demo')),"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS quiz_guests ("
            " id INTEGER PRIMARY KEY,"
            " guest_code TEXT NOT NULL UNIQUE,"
            " merged_user_id INTEGER,"
            " created_at TEXT NOT NULL)",
        ],
    },
    {
        # 里程碑 4:certvault 业务表(H08 §4)。blob 不入库:密文存
        # data/blobs/,库存相对路径 + SHA-256(H12 §一.4);
        # records.revoked_* 为 13-R-CV-5 撤销字段;engine_feedback 供
        # 13-R-CV-2 推荐器学习(非 PI)。
        "version": 4,
        "name": "certvault_business_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS cv_certs ("
            " id INTEGER PRIMARY KEY,"
            " owner_id INTEGER NOT NULL,"
            " cert_type TEXT NOT NULL"
            "  CHECK (cert_type IN ('idcard','driver','vehicle','license','other')),"
            " label TEXT NOT NULL,"
            " blob_path TEXT NOT NULL,"
            " blob_sha256 TEXT NOT NULL,"
            " thumb_b64 TEXT NOT NULL,"
            " is_demo INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_cv_certs_owner"
            " ON cv_certs(owner_id)",
            "CREATE TABLE IF NOT EXISTS cv_records ("
            " id INTEGER PRIMARY KEY,"
            " tracer_id TEXT NOT NULL UNIQUE,"
            " engine TEXT NOT NULL,"
            " cert_id INTEGER,"
            " cert_label TEXT NOT NULL DEFAULT '',"
            " cert_type TEXT NOT NULL DEFAULT '',"
            " issuer_id INTEGER NOT NULL,"
            " recipient TEXT NOT NULL DEFAULT '',"
            " purpose TEXT NOT NULL DEFAULT '',"
            " validity TEXT NOT NULL DEFAULT '',"
            " visible_text TEXT NOT NULL DEFAULT '',"
            " params_json TEXT NOT NULL DEFAULT '{}',"
            " engine_meta_json TEXT NOT NULL DEFAULT '{}',"
            " wm_bit_len INTEGER NOT NULL DEFAULT 0,"
            " wm_strength REAL NOT NULL DEFAULT 0,"
            " embed_w INTEGER NOT NULL DEFAULT 0,"
            " embed_h INTEGER NOT NULL DEFAULT 0,"
            " distort_seed INTEGER NOT NULL DEFAULT 0,"
            " archive_path TEXT NOT NULL DEFAULT '',"
            " archive_sha256 TEXT NOT NULL DEFAULT '',"
            " is_standalone INTEGER NOT NULL DEFAULT 0,"
            " is_demo INTEGER NOT NULL DEFAULT 0,"
            " revoked_at TEXT,"
            " revoked_by TEXT,"
            " created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_cv_records_issuer"
            " ON cv_records(issuer_id)",
            "CREATE TABLE IF NOT EXISTS cv_notes ("
            " id INTEGER PRIMARY KEY,"
            " record_id INTEGER NOT NULL UNIQUE,"
            " location_ct TEXT NOT NULL DEFAULT '',"
            " text_ct TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS cv_note_images ("
            " id INTEGER PRIMARY KEY,"
            " note_id INTEGER NOT NULL,"
            " blob_path TEXT NOT NULL,"
            " blob_sha256 TEXT NOT NULL,"
            " created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_cv_note_images_note"
            " ON cv_note_images(note_id)",
            "CREATE TABLE IF NOT EXISTS cv_engine_feedback ("
            " id INTEGER PRIMARY KEY,"
            " tracer_id TEXT NOT NULL,"
            " engine TEXT NOT NULL,"
            " medium TEXT NOT NULL DEFAULT '',"
            " hit INTEGER NOT NULL,"
            " created_at TEXT NOT NULL)",
        ],
    },
    {
        # 里程碑 4:certvault 本地登录扩展列(L02 §3 鉴权区)——TOTP 密钥
        # 信封密文(totp_pending 先存后 enable)、首登强改标记。
        "version": 5,
        "name": "certvault_local_auth_columns",
        "common": [
            "ALTER TABLE cv_users ADD COLUMN totp_enabled INTEGER"
            " NOT NULL DEFAULT 0",
            "ALTER TABLE cv_users ADD COLUMN totp_secret_ct TEXT"
            " NOT NULL DEFAULT ''",
            "ALTER TABLE cv_users ADD COLUMN totp_pending_ct TEXT"
            " NOT NULL DEFAULT ''",
            "ALTER TABLE cv_users ADD COLUMN must_change_password INTEGER"
            " NOT NULL DEFAULT 0",
        ],
    },
    {
        # 里程碑 5:nvr 业务表(L04 §4/§7)。设备密码信封密文;推送设备与
        # NVR 同表 kind 区分(NVR 无 push_token);每设备至多一条活动告警由
        # 部分唯一索引兜底;时间线统一承载状态跃迁与 channel_change。
        "version": 6,
        "name": "nvr_business_tables",
        "common": [
            "CREATE TABLE IF NOT EXISTS nvr_devices ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " kind TEXT NOT NULL DEFAULT 'nvr'"
            "  CHECK (kind IN ('nvr','push')),"
            " host TEXT NOT NULL DEFAULT '',"
            " port INTEGER NOT NULL DEFAULT 80,"
            " username TEXT NOT NULL DEFAULT '',"
            " password_ct TEXT NOT NULL DEFAULT '',"
            " region TEXT NOT NULL DEFAULT '',"
            " station TEXT NOT NULL DEFAULT '',"
            " enabled INTEGER NOT NULL DEFAULT 1,"
            " push_token TEXT,"
            " push_grace_seconds INTEGER NOT NULL DEFAULT 0,"
            " is_demo INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_nvr_devices_token"
            " ON nvr_devices(push_token) WHERE push_token IS NOT NULL",
            "CREATE TABLE IF NOT EXISTS nvr_check_results ("
            " id INTEGER PRIMARY KEY,"
            " device_id INTEGER NOT NULL,"
            " status TEXT NOT NULL,"
            " source TEXT NOT NULL DEFAULT 'patrol'"
            "  CHECK (source IN ('patrol','manual','push')),"
            " detail TEXT NOT NULL DEFAULT '',"
            " latency_ms INTEGER NOT NULL DEFAULT 0,"
            " checked_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_nvr_results_device"
            " ON nvr_check_results(device_id, checked_at)",
            "CREATE TABLE IF NOT EXISTS nvr_device_state ("
            " device_id INTEGER PRIMARY KEY,"
            " status TEXT NOT NULL DEFAULT 'unchecked',"
            " since TEXT NOT NULL,"
            " consecutive_fails INTEGER NOT NULL DEFAULT 0,"
            " ewma REAL NOT NULL DEFAULT 0,"
            " last_checked_at TEXT,"
            " last_detail TEXT NOT NULL DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS nvr_timeline ("
            " id INTEGER PRIMARY KEY,"
            " device_id INTEGER NOT NULL,"
            " event_type TEXT NOT NULL DEFAULT 'status_change',"
            " channel_no INTEGER,"
            " from_status TEXT NOT NULL DEFAULT '',"
            " to_status TEXT NOT NULL DEFAULT '',"
            " detail TEXT NOT NULL DEFAULT '',"
            " occurred_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_nvr_timeline_device"
            " ON nvr_timeline(device_id, occurred_at)",
            "CREATE TABLE IF NOT EXISTS nvr_channels ("
            " id INTEGER PRIMARY KEY,"
            " device_id INTEGER NOT NULL,"
            " channel_no INTEGER NOT NULL,"
            " name TEXT NOT NULL DEFAULT '',"
            " ip TEXT NOT NULL DEFAULT '',"
            " status TEXT NOT NULL DEFAULT 'unknown',"
            " removed INTEGER NOT NULL DEFAULT 0,"
            " first_seen TEXT NOT NULL,"
            " last_seen TEXT NOT NULL,"
            " UNIQUE(device_id, channel_no))",
            "CREATE TABLE IF NOT EXISTS nvr_alerts ("
            " id INTEGER PRIMARY KEY,"
            " device_id INTEGER NOT NULL,"
            " scope TEXT NOT NULL DEFAULT 'device'"
            "  CHECK (scope IN ('device','channel')),"
            " state TEXT NOT NULL DEFAULT 'firing'"
            "  CHECK (state IN ('firing','resolved')),"
            " trigger_status TEXT NOT NULL DEFAULT '',"
            " detail TEXT NOT NULL DEFAULT '',"
            " started_at TEXT NOT NULL,"
            " resolved_at TEXT,"
            " duration_seconds INTEGER)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_nvr_alerts_active"
            " ON nvr_alerts(device_id, scope) WHERE state = 'firing'",
            "CREATE TABLE IF NOT EXISTS nvr_notifications ("
            " id INTEGER PRIMARY KEY,"
            " alert_id INTEGER NOT NULL,"
            " channel TEXT NOT NULL,"
            " state TEXT NOT NULL DEFAULT 'pending'"
            "  CHECK (state IN ('pending','sent','failed','abandoned')),"
            " attempts INTEGER NOT NULL DEFAULT 0,"
            " next_attempt_at TEXT,"
            " payload TEXT NOT NULL DEFAULT '',"
            " last_error TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS nvr_reports ("
            " id INTEGER PRIMARY KEY,"
            " period_days INTEGER NOT NULL,"
            " generated_by TEXT NOT NULL DEFAULT 'template',"
            " reason TEXT NOT NULL DEFAULT '',"
            " facts_json TEXT NOT NULL DEFAULT '{}',"
            " content TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS nvr_api_keys ("
            " id INTEGER PRIMARY KEY,"
            " key_id TEXT NOT NULL UNIQUE,"
            " secret_ct TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " revoked_at TEXT)",
        ],
    },
]

# 业务子系统分册(v7/v8)聚合,版本序不变
from gd_storage.migrations_apps import APP_MIGRATIONS  # noqa: E402

MIGRATIONS = MIGRATIONS + APP_MIGRATIONS


def applied_versions(db: Database) -> set:
    """@brief 读取已应用版本集合;台账表不存在时返回空集"""
    try:
        rows = db.query("SELECT version FROM schema_migrations")
        return {row[0] for row in rows}
    except Exception:
        return set()


def apply_migrations(db: Database) -> list:
    """
    @brief  应用全部待执行迁移(幂等:重复执行不产生副作用,H12 §六.2)
    @param  db Database 实例
    @return 本次实际应用的版本号列表
    """
    done = applied_versions(db)
    newly_applied = []
    for migration in MIGRATIONS:
        version = migration["version"]
        if version in done:
            continue
        statements = list(migration["common"])
        dialect_key = "sqlite" if db.dialect == DIALECT_SQLITE else "postgres"
        statements += migration.get(dialect_key, [])
        for sql in statements:
            # PG:INTEGER PRIMARY KEY 无自增语义,统一转 SERIAL(生产实测,
            # 2026-07-19);显式赋值主键的表(如 nvr_device_state)不受影响。
            if db.dialect != DIALECT_SQLITE:
                sql = sql.replace(" INTEGER PRIMARY KEY", " SERIAL PRIMARY KEY")
            db.execute(sql)
        db.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) "
            "VALUES(?, ?, datetime('now'))" if db.dialect == DIALECT_SQLITE else
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES(?, ?, now())",
            (version, migration["name"]),
        )
        newly_applied.append(version)
    return newly_applied
