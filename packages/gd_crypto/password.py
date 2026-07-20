# -*- coding: utf-8 -*-
"""
@file    password.py
@brief   口令哈希(自描述串 + 登录透明重哈希,H04 §8.2.5)与
         HMAC 查询索引(手机号等 PI 的等值查询索引,H08 §1 / H12 §三.3)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_common.errors import CryptoError
from gd_crypto.suites import (
    ALG_HMAC_SHA256,
    ALG_HMAC_SM3,
    ICryptoSuite,
    get_suite,
    SUITE_GM,
    SUITE_INTL,
)

# 口令哈希前缀 → 所属套件(按存量记录自描述串选算法校验,H12 §三.2)
_HASH_PREFIX_TO_SUITE = {
    "$argon2id$": SUITE_INTL,
    "pbkdf2-sm3$": SUITE_GM,
}
# HMAC 索引自描述前缀 → 套件
_HMAC_ALG_TO_SUITE = {ALG_HMAC_SHA256: SUITE_INTL, ALG_HMAC_SM3: SUITE_GM}
HMAC_INDEX_SEP = "$"


def hash_password(password: str, suite: ICryptoSuite) -> str:
    """@brief 用当前套件生成自描述口令哈希 @return 哈希串"""
    return suite.hash_password(password)


def _suite_for_stored_hash(stored: str) -> ICryptoSuite:
    """@brief 按存量哈希串前缀反查套件;未知前缀抛 CryptoError"""
    for prefix, suite_name in _HASH_PREFIX_TO_SUITE.items():
        if stored.startswith(prefix):
            return get_suite(suite_name)
    raise CryptoError("口令哈希格式无法识别(非自描述串)")


def verify_password(password: str, stored: str, current: ICryptoSuite) -> tuple:
    """
    @brief  校验口令并支持透明重哈希:按存量串自带算法校验,成功且算法与当前套件
            不一致(或参数过时)时返回新哈希供调用方回写(H04 §8.2.5)
    @param  password 用户提交口令
    @param  stored   存量自描述哈希串
    @param  current  当前生效套件
    @return (是否匹配, 新哈希或 None)
    """
    legacy_suite = _suite_for_stored_hash(stored)
    if not legacy_suite.verify_password(password, stored):
        return False, None
    if current.password_needs_rehash(stored):
        return True, current.hash_password(password)
    return True, None


def hmac_index(value: str, key: bytes, suite: ICryptoSuite) -> str:
    """
    @brief  生成等值查询用 HMAC 索引(自描述:alg$hex),用于手机号等密文列的检索
    @param  value 明文值(如手机号);调用方不得将明文写日志(H04 §五)
    @param  key   HMAC 索引密钥(独立于主密钥,轮换须重建索引列,H12 §三.3)
    @param  suite 当前套件
    @return 形如 "HMAC-SHA256$<hex>" 的自描述索引串
    """
    mac = suite.hmac(key, value.encode("utf-8"))
    return f"{suite.hmac_alg}{HMAC_INDEX_SEP}{mac.hex()}"


def hmac_index_matches(value: str, stored_index: str, key: bytes) -> bool:
    """@brief 按存量索引自带 alg 选套件重算并比对(常数时间比较)"""
    alg, _, _ = stored_index.partition(HMAC_INDEX_SEP)
    if alg not in _HMAC_ALG_TO_SUITE:
        raise CryptoError(f"未知 HMAC 索引算法: {alg}")
    suite = get_suite(_HMAC_ALG_TO_SUITE[alg])
    import hmac as _hmac_mod
    return _hmac_mod.compare_digest(hmac_index(value, key, suite), stored_index)
