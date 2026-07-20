# -*- coding: utf-8 -*-
"""
@file    sm4.py
@brief   SM4 分组密码纯 Python 参考实现(GB/T 32907-2016):128bit 分组/密钥,
         32 轮非平衡 Feistel。零第三方依赖(H01 ARC-5),正确性由标准向量锚定
         (tests/test_f_crypto_suite.py)。仅供 gm 套件 GCM 模式调用。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import struct

_MASK32 = 0xFFFFFFFF
SM4_BLOCK_SIZE = 16      # 分组长度(字节)
SM4_KEY_SIZE = 16        # 密钥长度(字节)

# GB/T 32907 §6.2 S 盒(256 字节查找表)
_SBOX = bytes.fromhex(
    "d690e9fecce13db716b614c228fb2c05"
    "2b679a762abe04c3aa44132649860699"
    "9c4250f491ef987a33540b43edcfac62"
    "e4b31ca9c908e895 80df94fa758f3fa6"
    "4707a7fcf3731 7ba83593c19e6854fa8"
    "686b81b27164da8bf8eb0f4b70569d35"
    "1e240e5e6358d1a225227c3b01217887"
    "d40046579fd327524c3602e7a0c4c89e"
    "eabf8ad240c738b5a3f7f2cef96115a1"
    "e0ae5da49b341a55ad9332 30f58cb1e3"
    "1df6e22e8266ca60c02923ab0d534e6f"
    "d5db3745defd8e2f03ff6a726d6c5b51"
    "8d1baf92bbddbc7f11d95c411f105ad8"
    "0ac13188a5cd7bbd2d74d012b8e5b4b0"
    "8969974a0c96777e65b9f109c56ec684"
    "18f07dec3adc4d20 79ee5f3ed7cb3948".replace(" ", "")
)
# 系统参数 FK
_FK = (0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC)
# 固定参数 CK:ck_{i,j} = (4i+j)×7 mod 256(按标准公式生成,免手抄 32 项表)
_CK = tuple(
    ((4 * i * 7 % 256) << 24) | (((4 * i + 1) * 7 % 256) << 16)
    | (((4 * i + 2) * 7 % 256) << 8) | ((4 * i + 3) * 7 % 256)
    for i in range(32)
)


def _rotl(value: int, bits: int) -> int:
    """@brief 32 位循环左移"""
    return ((value << bits) | (value >> (32 - bits))) & _MASK32


def _tau(word: int) -> int:
    """@brief 非线性变换 τ:逐字节过 S 盒"""
    return ((_SBOX[(word >> 24) & 0xFF] << 24) | (_SBOX[(word >> 16) & 0xFF] << 16)
            | (_SBOX[(word >> 8) & 0xFF] << 8) | _SBOX[word & 0xFF])


def _t_enc(word: int) -> int:
    """@brief 合成置换 T(轮函数用):L(τ(x)),L = x⊕rotl2⊕rotl10⊕rotl18⊕rotl24"""
    b = _tau(word)
    return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)


def _t_key(word: int) -> int:
    """@brief 合成置换 T'(密钥扩展用):L'(τ(x)),L' = x⊕rotl13⊕rotl23"""
    b = _tau(word)
    return b ^ _rotl(b, 13) ^ _rotl(b, 23)


def expand_key(key: bytes) -> tuple:
    """
    @brief  密钥扩展:16 字节主密钥 → 32 个轮密钥(GB/T 32907 §7.3)
    @param  key 128bit 主密钥
    @return 轮密钥元组 rk[0..31](加密顺序;解密逆序使用)
    """
    if len(key) != SM4_KEY_SIZE:
        raise ValueError("SM4 密钥长度必须为 16 字节")
    k = list(struct.unpack(">4I", key))
    k = [k[i] ^ _FK[i] for i in range(4)]
    round_keys = []
    for i in range(32):
        rk = k[0] ^ _t_key(k[1] ^ k[2] ^ k[3] ^ _CK[i])
        round_keys.append(rk)
        k = [k[1], k[2], k[3], rk]
    return tuple(round_keys)


def encrypt_block(round_keys: tuple, block: bytes) -> bytes:
    """@brief 单块加密(16 字节),GCM-CTR 仅需正向 @return 密文块"""
    x = list(struct.unpack(">4I", block))
    for i in range(32):
        x = [x[1], x[2], x[3],
             x[0] ^ _t_enc(x[1] ^ x[2] ^ x[3] ^ round_keys[i])]
    return struct.pack(">4I", x[3], x[2], x[1], x[0])
