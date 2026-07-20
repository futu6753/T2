# -*- coding: utf-8 -*-
"""
@file    gcm.py
@brief   GCM 认证加密模式纯 Python 实现(NIST SP 800-38D):CTR 加密 + GHASH 认证。
         分组密码内核以函数注入(SM4 生产使用;AES 内核仅测试对拍,与 cryptography
         库输出比对锚定模式层正确性)。GHASH 采用标准逐位 GF(2^128) 乘法(实现
         简洁可审计;冒烟/迁移数据量级下性能足够)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hmac as _hmac_mod
import struct
from typing import Callable

from gd_common.errors import CryptoError

GCM_BLOCK = 16                    # GCM 分组长度(字节)
GCM_TAG_LEN = 16                  # 认证标签长度(字节)
_R_POLY = 0xE1000000000000000000000000000000  # GF(2^128) 既约多项式常数(反射表示)

BlockEncryptor = Callable[[bytes], bytes]     # 单块加密内核签名:16B → 16B


def _gf_mult(x: int, y: int) -> int:
    """@brief GF(2^128) 乘法(SP 800-38D §6.3 反射表示逐位算法)"""
    z, v = 0, x
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            z ^= v
        v = (v >> 1) ^ (_R_POLY if v & 1 else 0)
    return z


def _ghash(h: int, data: bytes) -> int:
    """@brief GHASH 累积:Y_i = (Y_{i-1} ⊕ X_i)·H,数据非整块末尾补零"""
    y = 0
    for offset in range(0, len(data), GCM_BLOCK):
        block = data[offset:offset + GCM_BLOCK].ljust(GCM_BLOCK, b"\x00")
        y = _gf_mult(y ^ int.from_bytes(block, "big"), h)
    return y


def _ctr_stream(encrypt: BlockEncryptor, j0: bytes, length: int) -> bytes:
    """@brief 生成 CTR 密钥流(计数器从 j0+1 起,SP 800-38D §7.1)"""
    counter = int.from_bytes(j0[12:], "big")
    prefix = j0[:12]
    stream = bytearray()
    for _ in range((length + GCM_BLOCK - 1) // GCM_BLOCK):
        counter = (counter + 1) & 0xFFFFFFFF
        stream += encrypt(prefix + struct.pack(">I", counter))
    return bytes(stream[:length])


def _compute_tag(encrypt: BlockEncryptor, h: int, j0: bytes,
                 aad: bytes, ciphertext: bytes) -> bytes:
    """@brief 计算认证标签:GHASH(AAD‖CT‖长度块) ⊕ E(J0)"""
    aad_pad = aad + b"\x00" * ((-len(aad)) % GCM_BLOCK)
    ct_pad = ciphertext + b"\x00" * ((-len(ciphertext)) % GCM_BLOCK)
    lengths = struct.pack(">QQ", len(aad) * 8, len(ciphertext) * 8)
    s = _ghash(h, aad_pad + ct_pad + lengths)
    e_j0 = int.from_bytes(encrypt(j0), "big")
    return (s ^ e_j0).to_bytes(GCM_BLOCK, "big")[:GCM_TAG_LEN]


def _derive_j0(encrypt: BlockEncryptor, h: int, nonce: bytes) -> bytes:
    """@brief 由 nonce 派生初始计数块 J0(96bit 快路径 / 任意长 GHASH 路径)"""
    if len(nonce) == 12:
        return nonce + b"\x00\x00\x00\x01"
    nonce_pad = nonce + b"\x00" * ((-len(nonce)) % GCM_BLOCK)
    material = nonce_pad + struct.pack(">QQ", 0, len(nonce) * 8)
    return _ghash(h, material).to_bytes(GCM_BLOCK, "big")


def gcm_encrypt(encrypt: BlockEncryptor, nonce: bytes, plaintext: bytes,
                aad: bytes) -> tuple:
    """
    @brief  GCM 加密
    @param  encrypt 单块加密内核(SM4 生产 / AES 对拍)
    @param  nonce   随机数(常规 12 字节)
    @param  plaintext 明文
    @param  aad     附加认证数据
    @return (密文, 16 字节标签)
    """
    h = int.from_bytes(encrypt(b"\x00" * GCM_BLOCK), "big")
    j0 = _derive_j0(encrypt, h, nonce)
    ciphertext = bytes(p ^ s for p, s in
                       zip(plaintext, _ctr_stream(encrypt, j0, len(plaintext))))
    return ciphertext, _compute_tag(encrypt, h, j0, aad, ciphertext)


def gcm_decrypt(encrypt: BlockEncryptor, nonce: bytes, ciphertext: bytes,
                tag: bytes, aad: bytes) -> bytes:
    """
    @brief  GCM 解密:先常数时间验标签,失败抛 CryptoError(H12 §三.4 完整性)
    @return 明文
    """
    h = int.from_bytes(encrypt(b"\x00" * GCM_BLOCK), "big")
    j0 = _derive_j0(encrypt, h, nonce)
    expected = _compute_tag(encrypt, h, j0, aad, ciphertext)
    if not _hmac_mod.compare_digest(expected, tag):
        raise CryptoError("AEAD 解密失败:密文或标签校验不通过")
    return bytes(c ^ s for c, s in
                 zip(ciphertext, _ctr_stream(encrypt, j0, len(ciphertext))))
