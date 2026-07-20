# -*- coding: utf-8 -*-
"""
@file    suites.py
@brief   密码套件抽象(H04 §八 / H01 ARC-8):国际套件 intl 为默认实现,
         国密套件 gm 预留扩展接口。业务代码零算法感知,MUST NOT 直呼具体算法库。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac as _hmac
import os
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gd_common.errors import ConfigError, CryptoError

# ---- 套件与算法名常量(禁魔法字符串,H07 L2-18) ----
SUITE_INTL = "intl"
SUITE_GM = "gm"
ALG_AES_256_GCM = "AES-256-GCM"
ALG_SM4_GCM = "SM4-GCM"
ALG_HMAC_SHA256 = "HMAC-SHA256"
ALG_HMAC_SM3 = "HMAC-SM3"
ALG_SHA256 = "SHA-256"
ALG_SM3 = "SM3"
ALG_ARGON2ID = "argon2id"
ALG_PBKDF2_SM3 = "pbkdf2-sm3"
GCM_NONCE_LEN = 12          # GCM 标准随机数长度(字节)
GCM_TAG_LEN = 16            # GCM 认证标签长度(字节),即存储完整性校验(H12 §三.4)
ENV_CRYPTO_SUITE = "CRYPTO_SUITE"


class ICryptoSuite(Protocol):
    """密码套件接口:AEAD 加解密、HMAC、摘要、口令哈希四类原语的统一契约。"""

    name: str
    aead_alg: str
    hmac_alg: str
    hash_alg: str
    password_alg: str

    def aead_encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> tuple:
        """@brief AEAD 加密 @return (ct, tag) 密文与认证标签分离返回"""
        ...

    def aead_decrypt(self, key: bytes, nonce: bytes, ct: bytes, tag: bytes, aad: bytes) -> bytes:
        """@brief AEAD 解密,标签校验失败必须抛 CryptoError @return 明文"""
        ...

    def hmac(self, key: bytes, data: bytes) -> bytes:
        """@brief 计算 HMAC @return 消息认证码"""
        ...

    def digest(self, data: bytes) -> bytes:
        """@brief 计算摘要 @return 哈希值"""
        ...

    def hash_password(self, password: str) -> str:
        """@brief 口令派生哈希 @return 自描述哈希串(H12 §三.2)"""
        ...

    def verify_password(self, password: str, stored: str) -> bool:
        """@brief 校验口令 @return 是否匹配"""
        ...

    def password_needs_rehash(self, stored: str) -> bool:
        """@brief 判断存量哈希是否需透明重哈希为当前套件算法(H04 §8.2.5)"""
        ...


class IntlSuite:
    """国际套件(默认):AES-256-GCM / HMAC-SHA256 / SHA-256 / argon2id。"""

    name = SUITE_INTL
    aead_alg = ALG_AES_256_GCM
    hmac_alg = ALG_HMAC_SHA256
    hash_alg = ALG_SHA256
    password_alg = ALG_ARGON2ID

    def __init__(self):
        self._ph = PasswordHasher()

    def aead_encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> tuple:
        """@brief AES-256-GCM 加密 @return (ct, tag)"""
        combined = AESGCM(key).encrypt(nonce, plaintext, aad)
        return combined[:-GCM_TAG_LEN], combined[-GCM_TAG_LEN:]

    def aead_decrypt(self, key: bytes, nonce: bytes, ct: bytes, tag: bytes, aad: bytes) -> bytes:
        """@brief AES-256-GCM 解密;密文/标签被改动即抛 CryptoError(GCM 完整性)"""
        try:
            return AESGCM(key).decrypt(nonce, ct + tag, aad)
        except Exception as exc:  # cryptography 抛 InvalidTag 等,统一收敛
            raise CryptoError("AEAD 解密失败:密文或标签校验不通过") from exc

    def hmac(self, key: bytes, data: bytes) -> bytes:
        """@brief HMAC-SHA256"""
        return _hmac.new(key, data, hashlib.sha256).digest()

    def digest(self, data: bytes) -> bytes:
        """@brief SHA-256 摘要"""
        return hashlib.sha256(data).digest()

    def hash_password(self, password: str) -> str:
        """@brief argon2id 口令哈希;argon2 原生串以 $argon2id$ 开头,天然自描述"""
        return self._ph.hash(password)

    def verify_password(self, password: str, stored: str) -> bool:
        """@brief 校验 argon2id 口令哈希"""
        try:
            return self._ph.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def password_needs_rehash(self, stored: str) -> bool:
        """@brief 存量非 argon2id(如 gm 套件的 pbkdf2-sm3)或参数过时则需重哈希"""
        if not stored.startswith("$argon2id$"):
            return True
        return self._ph.check_needs_rehash(stored)


class GmSuite:
    """
    国密套件真实现(纯 Python 参考实现,GAP-01 解除):SM4-GCM / HMAC-SM3 /
    SM3 / PBKDF2-HMAC-SM3。原语正确性由标准向量与 AES-GCM 对拍锚定
    (tests/test_f_crypto_suite.py)。SM2 签名走 IdP 密钥仓专线(apps/idp/keys)。
    """

    name = SUITE_GM
    aead_alg = ALG_SM4_GCM
    hmac_alg = ALG_HMAC_SM3
    hash_alg = ALG_SM3
    password_alg = ALG_PBKDF2_SM3
    # 纯 Python 参考实现下的迭代折中(自描述串携带迭代数,后续提档无缝校验存量);
    # 生产接入硬件/C 扩展国密 Provider 时 SHOULD 提至 ≥210000
    PBKDF2_ITERATIONS = 2000
    PBKDF2_SALT_LEN = 16
    _SM4_KEY_LEN = 16

    def _sm4_core(self, key: bytes):
        """@brief 由输入密钥派生 SM4 单块加密内核:32 字节 DEK/主密钥经 SM3
        单向压缩为 128bit SM4 密钥(确定性,加解密两侧一致)"""
        from gd_crypto.gm.sm3 import sm3_digest
        from gd_crypto.gm.sm4 import encrypt_block, expand_key
        sm4_key = key if len(key) == self._SM4_KEY_LEN else sm3_digest(key)[:self._SM4_KEY_LEN]
        round_keys = expand_key(sm4_key)
        return lambda block: encrypt_block(round_keys, block)

    def aead_encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> tuple:
        """@brief SM4-GCM 加密 @return (ct, tag)"""
        from gd_crypto.gm.gcm import gcm_encrypt
        return gcm_encrypt(self._sm4_core(key), nonce, plaintext, aad)

    def aead_decrypt(self, key: bytes, nonce: bytes, ct: bytes, tag: bytes, aad: bytes) -> bytes:
        """@brief SM4-GCM 解密;标签校验失败抛 CryptoError(gcm 层保证)"""
        from gd_crypto.gm.gcm import gcm_decrypt
        return gcm_decrypt(self._sm4_core(key), nonce, ct, tag, aad)

    def hmac(self, key: bytes, data: bytes) -> bytes:
        """@brief HMAC-SM3"""
        from gd_crypto.gm.sm3 import hmac_sm3
        return hmac_sm3(key, data)

    def digest(self, data: bytes) -> bytes:
        """@brief SM3 摘要"""
        from gd_crypto.gm.sm3 import sm3_digest
        return sm3_digest(data)

    def hash_password(self, password: str) -> str:
        """@brief PBKDF2-HMAC-SM3 口令哈希,自描述串 pbkdf2-sm3$iters$salt$hash"""
        from gd_crypto.gm.sm3 import pbkdf2_hmac_sm3
        salt = os.urandom(self.PBKDF2_SALT_LEN)
        derived = pbkdf2_hmac_sm3(password.encode("utf-8"), salt, self.PBKDF2_ITERATIONS)
        return f"pbkdf2-sm3${self.PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"

    def verify_password(self, password: str, stored: str) -> bool:
        """@brief 校验 pbkdf2-sm3 自描述串(常数时间比较);格式非法返回 False"""
        from gd_crypto.gm.sm3 import pbkdf2_hmac_sm3
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2-sm3":
            return False
        try:
            iterations, salt, expected = int(parts[1]), bytes.fromhex(parts[2]), parts[3]
        except ValueError:
            return False
        derived = pbkdf2_hmac_sm3(password.encode("utf-8"), salt, iterations)
        return _hmac.compare_digest(derived.hex(), expected)

    def password_needs_rehash(self, stored: str) -> bool:
        """@brief 非本套件格式(如存量 argon2id)或迭代数低于当前档 → 需透明重哈希"""
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2-sm3":
            return True
        try:
            return int(parts[1]) < self.PBKDF2_ITERATIONS
        except ValueError:
            return True


# 套件注册表:解密/验证按对象元数据选套件的唯一入口
_REGISTRY: dict = {SUITE_INTL: IntlSuite(), SUITE_GM: GmSuite()}
# AEAD 算法名 → 套件反查(解密按密文对象自带 alg 选算法,H04 §8.2.2)
_AEAD_TO_SUITE: dict = {ALG_AES_256_GCM: SUITE_INTL, ALG_SM4_GCM: SUITE_GM}
_HASH_TO_SUITE: dict = {ALG_SHA256: SUITE_INTL, ALG_SM3: SUITE_GM}


def get_suite(name: str) -> ICryptoSuite:
    """@brief 按套件名取实现 @param name intl|gm @return 套件实例"""
    if name not in _REGISTRY:
        raise ConfigError(f"未知密码套件: {name}(可选 {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def suite_for_aead_alg(alg: str) -> ICryptoSuite:
    """@brief 按密文对象的 alg 元数据反查套件(存量数据永远可解的关键)"""
    if alg not in _AEAD_TO_SUITE:
        raise CryptoError(f"未知 AEAD 算法: {alg}")
    return get_suite(_AEAD_TO_SUITE[alg])


def suite_for_hash_alg(alg: str) -> ICryptoSuite:
    """@brief 按审计记录的 alg 元数据反查套件(链校验逐条选算法,H12 §四)"""
    if alg not in _HASH_TO_SUITE:
        raise CryptoError(f"未知摘要算法: {alg}")
    return get_suite(_HASH_TO_SUITE[alg])


def current_suite(environ: dict = None) -> ICryptoSuite:
    """
    @brief  解析当前生效套件:CRYPTO_SUITE 环境变量,默认 intl(H04 §8.2.8)
    @param  environ 环境字典(测试注入用),缺省取 os.environ
    @return 套件实例;与 DEMO 模式正交,任何运行模式下套件行为一致(H05 §2)
    """
    env = os.environ if environ is None else environ
    return get_suite(env.get(ENV_CRYPTO_SUITE, SUITE_INTL))
