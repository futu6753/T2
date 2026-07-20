# -*- coding: utf-8 -*-
"""
@file    chain.py
@brief   审计链哈希纯函数(H12 §四):hash = H(id‖ts‖actor‖action‖detail‖ip‖prev_hash),
         摘要算法随套件、逐条记录 alg,链校验按记录 alg 逐条选算法。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_crypto.suites import ICryptoSuite, suite_for_hash_alg

CHAIN_FIELD_SEP = "\x1f"      # 单元分隔符:防止字段拼接歧义(如 "a"+"bc" 与 "ab"+"c")
GENESIS_PREV_HASH = ""        # 创世记录的 prev_hash 约定为空串


def compute_record_hash(record: dict, suite: ICryptoSuite) -> str:
    """
    @brief  计算单条审计记录链哈希
    @param  record 含 id/ts/actor/action/detail/ip/prev_hash 的字典
    @param  suite  写入时的当前套件(alg 随记录落库)
    @return 哈希 hex 串
    """
    material = CHAIN_FIELD_SEP.join([
        str(record["id"]), str(record["ts"]), str(record["actor"]),
        str(record["action"]), str(record["detail"]), str(record["ip"]),
        str(record["prev_hash"]),
    ])
    return suite.digest(material.encode("utf-8")).hex()


def verify_record_hash(record: dict) -> bool:
    """
    @brief  按记录自带 alg 选算法重算并比对(套件切换不破坏链完整性,H04 §8.2.4)
    @param  record 含 alg 与 hash 字段的完整审计记录
    @return 哈希是否一致
    """
    suite = suite_for_hash_alg(record["alg"])
    return compute_record_hash(record, suite) == record["hash"]
