# -*- coding: utf-8 -*-
"""
@file    envelope.py
@brief   密文信封:每对象独立 DEK + 主密钥包裹,自描述 JSON 统一格式(H12 §三)。
         解密按对象自带 alg/kid 选算法——套件切换只影响新写入,存量永远可解(H04 §8.2.2)。
         v2.0(13-R-IDP-2):支持双写窗口——CRYPTO_DUAL_WRITE=<副套件名> 生效期间,
         新信封追加 dual 段(副套件完整平行密文),迁移期新旧套件均可解可验;
         主段解密失败(如套件 Provider 不可用)时自动回退 dual 段。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import json
import os
import secrets

from gd_common.errors import CryptoError
from gd_crypto.keyring import MasterKeyRing
from gd_crypto.suites import GCM_NONCE_LEN, get_suite, ICryptoSuite, suite_for_aead_alg

ENV_DUAL_WRITE = "CRYPTO_DUAL_WRITE"    # 双写窗口开关:值为副套件名(intl|gm)


def resolve_dual_suite(primary: ICryptoSuite, environ: dict = None) -> ICryptoSuite:
    """@brief 解析双写副套件:未开窗口或与主套件同名返回 None(13-R-IDP-2)"""
    env = os.environ if environ is None else environ
    name = env.get(ENV_DUAL_WRITE, "").strip()
    if not name or name == primary.name:
        return None
    return get_suite(name)

ENVELOPE_VERSION = 1     # 信封格式版本号
DEK_LEN = 32             # 数据加密密钥长度(字节)
DEK_ID_PREFIX = "dk"     # DEK 标识前缀
DEK_ID_RAND_BYTES = 6    # DEK 标识随机部分字节数


def _b64e(data: bytes) -> str:
    """@brief base64 编码为字符串"""
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    """@brief base64 解码,损坏即抛 CryptoError"""
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:
        raise CryptoError("信封字段 base64 解码失败") from exc


def _seal_once(plaintext: bytes, ring: MasterKeyRing, suite: ICryptoSuite,
               aad: bytes) -> dict:
    """@brief 单套件信封构造(encrypt_envelope 主段与 dual 段共用)"""
    dek = secrets.token_bytes(DEK_LEN)
    dek_id = DEK_ID_PREFIX + secrets.token_hex(DEK_ID_RAND_BYTES)
    data_nonce = os.urandom(GCM_NONCE_LEN)
    ct, tag = suite.aead_encrypt(dek, data_nonce, plaintext, aad)

    master_kid, master_key = ring.current()
    wrap_nonce = os.urandom(GCM_NONCE_LEN)
    wrapped_ct, wrapped_tag = suite.aead_encrypt(master_key, wrap_nonce, dek, b"")
    return {
        "v": ENVELOPE_VERSION,
        "alg": suite.aead_alg,
        "kid": dek_id,
        "nonce": _b64e(data_nonce),
        "ct": _b64e(ct),
        "tag": _b64e(tag),
        "wrapped_dek": {
            "alg": suite.aead_alg,
            "kid": master_kid,
            "nonce": _b64e(wrap_nonce),
            "ct": _b64e(wrapped_ct),
            "tag": _b64e(wrapped_tag),
        },
    }


def encrypt_envelope(plaintext: bytes, ring: MasterKeyRing, suite: ICryptoSuite,
                     aad: bytes = b"", environ: dict = None) -> dict:
    """
    @brief  信封加密:随机 DEK 加密数据,当前主密钥包裹 DEK,产出自描述 JSON 信封;
            双写窗口开启时(CRYPTO_DUAL_WRITE)追加副套件平行 dual 段(13-R-IDP-2)
    @param  plaintext 明文字节串(调用方负责用后释放,不落明文临时文件,H04 §六)
    @param  ring      主密钥环
    @param  suite     当前写入套件(读取时按信封元数据选套件,与本参数无关)
    @param  aad       附加认证数据(可选,绑定业务上下文)
    @param  environ   环境字典(测试注入用),缺省取 os.environ
    @return 信封 dict:{v, alg, kid, nonce, ct, tag, wrapped_dek{...}[, dual{...}]}
    """
    envelope = _seal_once(plaintext, ring, suite, aad)
    dual_suite = resolve_dual_suite(suite, environ)
    if dual_suite is not None:
        envelope["dual"] = _seal_once(plaintext, ring, dual_suite, aad)
    return envelope


def decrypt_envelope(envelope: dict, ring: MasterKeyRing, aad: bytes = b"") -> bytes:
    """
    @brief  信封解密:按 wrapped_dek.kid 选主密钥解包 DEK,再按信封 alg 解数据。
            密文/标签按位翻转 MUST 解密失败(GCM 完整性,H09 §二 A.5)。
            主段解密抛错且存在 dual 段时,回退副套件段再试一次(13-R-IDP-2)。
    @param  envelope 信封 dict(可来自 JSON 反序列化)
    @param  ring     主密钥环
    @param  aad      附加认证数据,须与加密时一致
    @return 明文字节串
    """
    if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
        raise CryptoError("信封格式非法或版本不受支持")
    try:
        return _open_once(envelope, ring, aad)
    except CryptoError:
        dual = envelope.get("dual")
        if isinstance(dual, dict):
            return _open_once(dual, ring, aad)
        raise


def _open_once(envelope: dict, ring: MasterKeyRing, aad: bytes) -> bytes:
    """@brief 单段信封解密(decrypt_envelope 主段与 dual 段共用)"""
    wrapped = envelope.get("wrapped_dek") or {}
    wrap_suite = suite_for_aead_alg(wrapped.get("alg", ""))
    master_key = ring.get(wrapped.get("kid", ""))
    dek = wrap_suite.aead_decrypt(
        master_key, _b64d(wrapped["nonce"]), _b64d(wrapped["ct"]), _b64d(wrapped["tag"]), b""
    )
    data_suite = suite_for_aead_alg(envelope.get("alg", ""))
    return data_suite.aead_decrypt(
        dek, _b64d(envelope["nonce"]), _b64d(envelope["ct"]), _b64d(envelope["tag"]), aad
    )


def envelope_to_json(envelope: dict) -> str:
    """@brief 信封序列化为紧凑 JSON(入库统一格式:PG=JSONB / SQLite=TEXT)"""
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def envelope_from_json(raw: str) -> dict:
    """@brief 从 JSON 字符串还原信封,损坏即抛 CryptoError"""
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CryptoError("信封 JSON 反序列化失败") from exc
