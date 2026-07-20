# -*- coding: utf-8 -*-
"""
@file    audit.py
@brief   统一审计写入器与链校验器(H12 §四):审计写入路径是全平台唯一实现,
         各子系统 MUST NOT 自写。只增不改由触发器保证;写入全局串行防并发分叉;
         哈希算法随套件、逐条记录 alg。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import json

from gd_common.errors import AuditTamperError
from gd_common.jsonlog import get_logger
from gd_crypto.chain import compute_record_hash, GENESIS_PREV_HASH, verify_record_hash
from gd_crypto.suites import ICryptoSuite
from gd_storage.database import Database
from gd_storage.events import ALL_EVENTS

_logger = get_logger("gd_storage.audit")
SECRET_DETAIL_MASK = "已修改"     # secret 类参数变更只记"已修改",不落新旧值(H03 §8)


def _utc_now_iso() -> str:
    """@brief 当前 UTC 时间 ISO 串(时间列一律 UTC 存储,H12 §二)"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class AuditWriter:
    """审计写入器:串行事务内读链尾→计算链哈希→插入,任何模式下不可简化(H05 §1.3)。"""

    def __init__(self, db: Database, suite: ICryptoSuite):
        self._db = db
        self._suite = suite

    def append(self, actor: str, action: str, detail: dict, ip: str) -> int:
        """
        @brief  追加一条审计记录(全局串行:SQLite BEGIN IMMEDIATE / PG advisory lock)
        @param  actor  操作者账号或系统标识
        @param  action 事件类型,MUST 出自统一事件字典(gd_storage.events)
        @param  detail 结构化详情(禁含口令/验证码/密钥,H04 §五)
        @param  ip     来源 IP
        @return 新记录 id
        """
        if action not in ALL_EVENTS:
            # 事件字典是唯一清单来源:未登记事件视为编码错误(H04 ai_directives)
            raise ValueError(f"审计事件 {action} 未登记于统一事件字典(H04 §三.a)")
        ts = _utc_now_iso()
        detail_json = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        with self._db.serial_txn() as txn:
            txn.execute("SELECT id, hash FROM audit_logs ORDER BY id DESC LIMIT 1")
            tail = txn.fetchone()
            new_id = (tail[0] + 1) if tail else 1
            prev_hash = tail[1] if tail else GENESIS_PREV_HASH
            record = {"id": new_id, "ts": ts, "actor": actor, "action": action,
                      "detail": detail_json, "ip": ip, "prev_hash": prev_hash}
            record_hash = compute_record_hash(record, self._suite)
            txn.execute(
                "INSERT INTO audit_logs(id, ts, actor, action, detail, ip, alg, prev_hash, hash) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, ts, actor, action, detail_json, ip,
                 self._suite.hash_alg, prev_hash, record_hash),
            )
        return new_id


def verify_chain(db: Database) -> int:
    """
    @brief  全链校验:逐条按记录自带 alg 选算法重算哈希并核对前向链接;
            检测到篡改/断链抛 AuditTamperError(管理页"一键校验"复用本函数)
    @param  db Database 实例
    @return 校验通过的记录条数
    """
    rows = db.query(
        "SELECT id, ts, actor, action, detail, ip, alg, prev_hash, hash "
        "FROM audit_logs ORDER BY id ASC"
    )
    expected_prev = GENESIS_PREV_HASH
    for row in rows:
        record = dict(zip(
            ("id", "ts", "actor", "action", "detail", "ip", "alg", "prev_hash", "hash"), row
        ))
        if record["prev_hash"] != expected_prev:
            raise AuditTamperError(f"审计链断链于 id={record['id']}(prev_hash 不连续)")
        if not verify_record_hash(record):
            raise AuditTamperError(f"审计记录 id={record['id']} 哈希校验失败(疑似篡改)")
        expected_prev = record["hash"]
    _logger.info("审计链校验通过", extra={"ctx": {"count": len(rows)}})
    return len(rows)
