# -*- coding: utf-8 -*-
"""
@file    sm2.py
@brief   SM2 数字签名纯 Python 参考实现(GB/T 32918.2-2016,曲线 sm2p256v1):
         签名/验签 + ZA 用户杂凑(默认 ID)。用于 gm 套件下 OIDC id_token 的
         SM2-with-SM3 签名(H04 §8.1)。仿射坐标 + 费马小定理求逆,
         点运算正确性由"曲线方程 + 签验回环 + 篡改拒绝"测试锚定。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets
import struct

from gd_common.errors import CryptoError
from gd_crypto.gm.sm3 import sm3_digest

# 曲线 sm2p256v1 参数(GB/T 32918.5 附录 A)
P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
A = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
B = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0

COORD_LEN = 32                          # 坐标/标量字节长
SIGNATURE_LEN = 64                      # 签名 r‖s 原始拼接长度
DEFAULT_USER_ID = b"1234567812345678"   # GB/T 32918 缺省用户身份标识


def _inv_mod(value: int, modulus: int) -> int:
    """@brief 模逆(费马小定理;modulus 为素数)"""
    return pow(value, modulus - 2, modulus)

def _point_add(p1: tuple, p2: tuple) -> tuple:
    """@brief 仿射坐标点加(含倍点分支);None 表示无穷远点"""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * _inv_mod(2 * y1, P) % P
    else:
        slope = (y2 - y1) * _inv_mod(x2 - x1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _scalar_mult(scalar: int, point: tuple) -> tuple:
    """@brief 标量乘(二进制自左向右)"""
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def generate_keypair() -> tuple:
    """@brief 生成 SM2 密钥对 @return (私钥 int, 公钥 (x, y))"""
    private = secrets.randbelow(N - 2) + 1
    return private, _scalar_mult(private, (GX, GY))


def public_key_from_private(private: int) -> tuple:
    """@brief 由私钥派生公钥 @return (x, y)"""
    return _scalar_mult(private, (GX, GY))


def _za(public: tuple, user_id: bytes) -> bytes:
    """@brief 用户杂凑值 ZA = SM3(ENTL‖ID‖a‖b‖Gx‖Gy‖Px‖Py)(GB/T 32918.2 §5.5)"""
    entl = struct.pack(">H", len(user_id) * 8)
    material = entl + user_id
    for value in (A, B, GX, GY, public[0], public[1]):
        material += value.to_bytes(COORD_LEN, "big")
    return sm3_digest(material)


def _message_digest(message: bytes, public: tuple, user_id: bytes) -> int:
    """@brief e = SM3(ZA‖M) 转整数"""
    return int.from_bytes(sm3_digest(_za(public, user_id) + message), "big")


def sign(message: bytes, private: int, user_id: bytes = DEFAULT_USER_ID) -> bytes:
    """
    @brief  SM2 签名(GB/T 32918.2 §6.1)
    @param  message 待签消息
    @param  private 私钥
    @param  user_id 用户身份标识(缺省标准 ID)
    @return 64 字节原始签名 r‖s
    """
    public = public_key_from_private(private)
    e = _message_digest(message, public, user_id)
    while True:
        k = secrets.randbelow(N - 1) + 1
        x1, _ = _scalar_mult(k, (GX, GY))
        r = (e + x1) % N
        if r == 0 or r + k == N:
            continue
        s = _inv_mod(1 + private, N) * (k - r * private) % N
        if s == 0:
            continue
        return r.to_bytes(COORD_LEN, "big") + s.to_bytes(COORD_LEN, "big")


def verify(message: bytes, signature: bytes, public: tuple,
           user_id: bytes = DEFAULT_USER_ID) -> bool:
    """
    @brief  SM2 验签(GB/T 32918.2 §7.1)
    @param  signature 64 字节 r‖s
    @param  public    公钥 (x, y)
    @return 是否验签通过
    """
    if len(signature) != SIGNATURE_LEN:
        return False
    r = int.from_bytes(signature[:COORD_LEN], "big")
    s = int.from_bytes(signature[COORD_LEN:], "big")
    if not (1 <= r <= N - 1 and 1 <= s <= N - 1):
        return False
    t = (r + s) % N
    if t == 0:
        return False
    e = _message_digest(message, public, user_id)
    point = _point_add(_scalar_mult(s, (GX, GY)), _scalar_mult(t, public))
    if point is None:
        return False
    return (e + point[0]) % N == r


def validate_public_key(public: tuple) -> None:
    """@brief 校验公钥在曲线上(JWKS 装载入口防伪造点,H04 §8.2.3)"""
    x, y = public
    if not (0 < x < P and 0 < y < P):
        raise CryptoError("SM2 公钥坐标越界")
    if (y * y - (x * x * x + A * x + B)) % P != 0:
        raise CryptoError("SM2 公钥不在曲线上")
