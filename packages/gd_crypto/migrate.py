# -*- coding: utf-8 -*-
"""
@file    migrate.py
@brief   密码套件迁移核心(H04 §8.2.6 / 13-R-IDP-2):逐对象"解旧包新",
         幂等(alg 已达目标即跳过)、断点续迁(状态文件游标)、迁移开始/进度/
         完成审计锚点。覆盖三类对象:DB 信封列(含 HMAC 索引重算)、文件形态
         信封(证件/笔记图 blob)、口令哈希(只统计——登录透明重哈希,§8.2.5)。
         CLI 壳见 scripts/migrate_crypto_suite.py(含强制备份守卫)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os

from gd_common.jsonlog import get_logger
from gd_crypto.envelope import decrypt_envelope, encrypt_envelope, envelope_from_json, envelope_to_json
from gd_crypto.keyring import MasterKeyRing
from gd_crypto.password import hmac_index
from gd_crypto.suites import ICryptoSuite

_logger = get_logger("gd_crypto.migrate")

# 声明式迁移目标:表 → (信封列清单, AAD 生成器输入列, AAD 模板)
# AAD 语义与各业务写入点严格一致(变更此表 MUST 与写入点同步评审)
DB_ENVELOPE_TARGETS = (
    {"table": "idp_users", "id_col": "id", "columns": ("phone_ct", "totp_secret_ct"),
     "aad": b"", "reindex": {"source": "phone_ct", "index_col": "phone_index"}},
    {"table": "cv_users", "id_col": "id",
     "columns": ("totp_secret_ct", "totp_pending_ct"), "aad": b"cv_totp_secret"},
    {"table": "cv_notes", "id_col": "id", "columns": ("location_ct", "text_ct"),
     "aad": b"cv_note"},
    {"table": "nvr_devices", "id_col": "id", "columns": ("password_ct",),
     "aad": b"nvr_device_password"},
    {"table": "nvr_api_keys", "id_col": "id", "columns": ("secret_ct",),
     "aad": b"nvr_api_key"},
    {"table": "f3d_external_keys", "id_col": "id", "columns": ("secret_ct",),
     "aad": b"f3d-external"},
)
# 文件形态信封:表提供 blob_path 与 AAD 上下文
FILE_ENVELOPE_TARGETS = (
    {"table": "cv_certs", "id_col": "id", "path_col": "blob_path",
     "aad_template": "cv_cert:{owner_id}", "aad_cols": ("owner_id",)},
    {"table": "cv_note_images", "id_col": "id", "path_col": "blob_path",
     "aad_template": "cv_note", "aad_cols": ()},
)
# 口令哈希列(只统计,不迁移:登录成功后透明重哈希,H04 §8.2.5)
PASSWORD_TABLES = (("idp_users", "password_hash"), ("cv_users", "password_hash"),
                   ("nvr_users", "password_hash"), ("f3d_users", "password_hash"))
# 说明:quiz_users 无本地口令列(纯 SSO/游客双身份,H03 §6),不在统计范围
PROGRESS_BATCH = 50        # 每处理 N 个对象写一条进度审计锚点
MIGRATE_ACTOR = "migrate_crypto_suite"
LOCAL_IP = "127.0.0.1"


def _table_exists(db, table: str) -> bool:
    """@brief 探测表是否存在(六库同构 schema,分库部署时缺表即跳过)"""
    try:
        db.query(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 表名出自本文件白名单
        return True
    except Exception:  # noqa: BLE001 方言差异统一按"不存在"处理
        return False


class MigrationState:
    """断点状态文件:{target, done:[阶段键], cursor:{阶段键: last_id}, counts}。"""

    def __init__(self, path: str, target: str):
        self.path = path
        self.data = {"target": target, "done": [], "cursor": {}, "counts": {}}
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if loaded.get("target") == target:
                self.data = loaded
            else:
                _logger.warning("状态文件目标套件不符,忽略旧状态重新开始",
                                extra={"old": loaded.get("target"), "new": target})

    def save(self):
        """@brief 原子落盘(tmp+rename,断点一致性)"""
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False)
        os.replace(tmp, self.path)

    def phase_done(self, key: str) -> bool:
        """@brief 阶段是否已完成"""
        return key in self.data["done"]

    def mark_done(self, key: str):
        """@brief 标记阶段完成并落盘"""
        if key not in self.data["done"]:
            self.data["done"].append(key)
        self.save()

    def cursor(self, key: str) -> int:
        """@brief 取阶段断点游标(未开始为 0)"""
        return int(self.data["cursor"].get(key, 0))

    def advance(self, key: str, last_id: int, migrated: int, skipped: int):
        """@brief 推进游标并累计计数"""
        self.data["cursor"][key] = last_id
        counts = self.data["counts"].setdefault(key, {"migrated": 0, "skipped": 0})
        counts["migrated"] += migrated
        counts["skipped"] += skipped
        self.save()


def _reseal(raw_json: str, ring: MasterKeyRing, target: ICryptoSuite,
            aad: bytes) -> str:
    """@brief 单对象解旧包新;已达目标套件返回 None(幂等跳过)。
    显式 environ={} 关闭双写——迁移产物统一为目标套件单段。
    target=None 为主密钥轮换语义:算法不变,仅当前主密钥重包(H06-E10)。"""
    envelope = envelope_from_json(raw_json)
    if target is None:
        wrapped_kid = (envelope.get("wrapped_dek") or {}).get("kid")
        if wrapped_kid == ring.current_kid:
            return None
        from gd_crypto.suites import suite_for_aead_alg
        target = suite_for_aead_alg(envelope.get("alg", ""))
    elif envelope.get("alg") == target.aead_alg and "dual" not in envelope:
        return None
    plaintext = decrypt_envelope(envelope, ring, aad=aad)
    return envelope_to_json(encrypt_envelope(plaintext, ring, target,
                                             aad=aad, environ={}))


def _migrate_db_target(db, ring, target, spec, state, audit) -> None:
    """@brief 迁移单表的信封列(游标分批,批间可安全中断)"""
    key = f"db:{spec['table']}"
    if state.phase_done(key) or not _table_exists(db, spec["table"]):
        state.mark_done(key)
        return
    columns = ", ".join(spec["columns"])
    reindex = spec.get("reindex")
    while True:
        rows = db.query(
            f"SELECT {spec['id_col']}, {columns} FROM {spec['table']}"  # noqa: S608
            f" WHERE {spec['id_col']} > ? ORDER BY {spec['id_col']} LIMIT ?",
            (state.cursor(key), PROGRESS_BATCH))
        if not rows:
            break
        migrated = skipped = 0
        for row in rows:
            row_id, values = row[0], row[1:]
            for col, raw in zip(spec["columns"], values):
                if not raw:
                    continue
                new_json = _reseal(raw, ring, target, spec["aad"])
                if new_json is None:
                    skipped += 1
                    continue
                db.execute(f"UPDATE {spec['table']} SET {col} = ?"  # noqa: S608
                           f" WHERE {spec['id_col']} = ?", (new_json, row_id))
                if reindex and col == reindex["source"]:
                    parsed = envelope_from_json(new_json)
                    from gd_crypto.suites import suite_for_aead_alg
                    index_suite = (target if target is not None
                                   else suite_for_aead_alg(parsed["alg"]))
                    plain = decrypt_envelope(parsed, ring,
                                             aad=spec["aad"]).decode("utf-8")
                    db.execute(
                        f"UPDATE {spec['table']} SET {reindex['index_col']} = ?"  # noqa: S608
                        f" WHERE {spec['id_col']} = ?",
                        (hmac_index(plain, ring.current_key, index_suite), row_id))
                migrated += 1
        state.advance(key, rows[-1][0], migrated, skipped)
        audit.append(MIGRATE_ACTOR, "crypto_migration_progress",
                     {"phase": key, "last_id": rows[-1][0],
                      "migrated": migrated, "skipped": skipped}, LOCAL_IP)
    state.mark_done(key)


def _migrate_file_target(db, ring, target, spec, blob_dir, state, audit) -> None:
    """@brief 迁移文件形态信封(按表行驱动定位文件,原子替换)"""
    key = f"file:{spec['table']}"
    if state.phase_done(key) or not blob_dir or not _table_exists(db, spec["table"]):
        state.mark_done(key)
        return
    select_cols = ", ".join((spec["id_col"], spec["path_col"]) + spec["aad_cols"])
    while True:
        rows = db.query(
            f"SELECT {select_cols} FROM {spec['table']}"  # noqa: S608
            f" WHERE {spec['id_col']} > ? ORDER BY {spec['id_col']} LIMIT ?",
            (state.cursor(key), PROGRESS_BATCH))
        if not rows:
            break
        migrated = skipped = 0
        for row in rows:
            aad_ctx = dict(zip(spec["aad_cols"], row[2:]))
            aad = spec["aad_template"].format(**aad_ctx).encode("utf-8")
            full_path = os.path.join(blob_dir, row[1])
            if not os.path.exists(full_path):
                skipped += 1
                continue
            with open(full_path, "r", encoding="utf-8") as handle:
                new_json = _reseal(handle.read(), ring, target, aad)
            if new_json is None:
                skipped += 1
                continue
            tmp = full_path + ".migrate"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(new_json)
            os.replace(tmp, full_path)
            migrated += 1
        state.advance(key, rows[-1][0], migrated, skipped)
        audit.append(MIGRATE_ACTOR, "crypto_migration_progress",
                     {"phase": key, "last_id": rows[-1][0],
                      "migrated": migrated, "skipped": skipped}, LOCAL_IP)
    state.mark_done(key)


def _password_report(db, target) -> dict:
    """@brief 口令哈希分布统计(迁移不触碰:透明重哈希承接,H04 §8.2.5)"""
    report = {}
    for table, col in PASSWORD_TABLES:
        try:
            rows = db.query(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL")  # noqa: S608
        except Exception:  # noqa: BLE001 缺表/缺列(分库部署形态)按无口令数据处理
            continue
        pending = sum(1 for (stored,) in rows if stored
                      and target.password_needs_rehash(stored))
        report[table] = {"total": len(rows), "pending_rehash": pending}
    return report


def run_key_rotation(db, ring: MasterKeyRing, audit, blob_dir: str = None,
                     state_file: str = None) -> dict:
    """
    @brief  主密钥轮换(H06-E10 轮换=迁移):环境须同时注入新钥(MASTER_KEY_HEX)
            与旧钥(OLD_MASTER_KEY_HEX);逐对象按原算法解包并用当前主密钥重包,
            幂等、可断点。完成写 master_key_rotated 审计锚点。
    @return 汇总报告 dict
    """
    state = MigrationState(state_file, f"rotate:{ring.current_kid}")
    for spec in DB_ENVELOPE_TARGETS:
        _migrate_db_target(db, ring, None, spec, state, audit)
    for spec in FILE_ENVELOPE_TARGETS:
        _migrate_file_target(db, ring, None, spec, blob_dir, state, audit)
    total = sum(c["migrated"] for c in state.data["counts"].values())
    audit.append(MIGRATE_ACTOR, "master_key_rotated",
                 {"to_kid": ring.current_kid, "rewrapped": total}, LOCAL_IP)
    return {"to_kid": ring.current_kid, "rewrapped": total,
            "phases": state.data["counts"]}


def run_migration(db, ring: MasterKeyRing, target: ICryptoSuite, audit,
                  blob_dir: str = None, state_file: str = None) -> dict:
    """
    @brief  执行套件迁移全流程(可断点续迁:同一 state_file 重跑自动续接)
    @param  db         目标数据库(六库同构,多库部署逐库调用)
    @param  ring       主密钥环
    @param  target     目标套件
    @param  audit      统一审计写入器(锚点:开始/进度/完成)
    @param  blob_dir   文件信封目录(certvault 数据目录;无则跳过文件阶段)
    @param  state_file 断点状态文件路径(None 则不落断点)
    @return 汇总报告 dict
    """
    state = MigrationState(state_file, target.name)
    if not state.data["done"] and not state.data["cursor"]:
        audit.append(MIGRATE_ACTOR, "crypto_migration_started",
                     {"target": target.name}, LOCAL_IP)
    for spec in DB_ENVELOPE_TARGETS:
        _migrate_db_target(db, ring, target, spec, state, audit)
    for spec in FILE_ENVELOPE_TARGETS:
        _migrate_file_target(db, ring, target, spec, blob_dir, state, audit)
    report = {"target": target.name, "phases": state.data["counts"],
              "password_pending_rehash": _password_report(db, target)}
    audit.append(MIGRATE_ACTOR, "crypto_migration_completed",
                 {"target": target.name,
                  "migrated_total": sum(c["migrated"]
                                        for c in state.data["counts"].values())},
                 LOCAL_IP)
    return report
