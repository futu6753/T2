# -*- coding: utf-8 -*-
"""
@file    suite_state.py
@brief   密码套件启动状态守卫(H04 §8.2.8 / H09 F.5):对比 platform_meta 记录的
         上次生效套件,发生切换时写 crypto_suite_changed 审计锚点;当前为 gm 时
         输出大写提示日志。各 app context 装配尾部调用一次。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime

from gd_common.jsonlog import get_logger
from gd_crypto.suites import ICryptoSuite, SUITE_GM
from gd_storage.audit import AuditWriter
from gd_storage.database import Database
from gd_storage.events import CRYPTO_SUITE_CHANGED

_logger = get_logger("gd_storage.suite_state")
META_KEY_CRYPTO_SUITE = "crypto_suite"
SYSTEM_ACTOR = "system"
LOCAL_IP = "127.0.0.1"


def record_suite_startup(db: Database, audit: AuditWriter, suite: ICryptoSuite) -> bool:
    """
    @brief  启动期套件状态登记:与上次不同(含首次显式 gm)则写切换审计锚点;
            gm 生效时输出大写提示日志(H04 §8.2.8)
    @param  db    应用数据库(platform_meta 所在)
    @param  audit 统一审计写入器
    @param  suite 当前生效套件
    @return 本次是否写入了 crypto_suite_changed 事件
    """
    rows = db.query("SELECT value FROM platform_meta WHERE key = ?",
                    (META_KEY_CRYPTO_SUITE,))
    previous = rows[0][0] if rows else None
    if suite.name == SUITE_GM:
        _logger.warning("CRYPTO_SUITE=GM 国密套件已启用(纯 PYTHON 参考实现);"
                        "存量 INTL 数据按对象自描述元数据继续可解可验(H04 §8.2)")
    changed = previous != suite.name and not (previous is None and suite.name != SUITE_GM)
    if changed:
        audit.append(SYSTEM_ACTOR, CRYPTO_SUITE_CHANGED,
                     {"from": previous or "(初始)", "to": suite.name}, LOCAL_IP)
    if previous != suite.name:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        db.execute(
            "INSERT INTO platform_meta(key, value, updated_at) VALUES(?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (META_KEY_CRYPTO_SUITE, suite.name, now))
    return changed
