# -*- coding: utf-8 -*-
"""
@file    __init__.py
@brief   gd_crypto:密码学统一抽象共享库(H01 ARC-8)。业务代码只允许 import 本包,
         MUST NOT 直呼具体算法库(代码评审发现即否决,H04 ai_directives)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_crypto.chain import compute_record_hash, verify_record_hash
from gd_crypto.context_token import issue_context, renew_context, verify_context
from gd_crypto.envelope import (
    decrypt_envelope,
    encrypt_envelope,
    envelope_from_json,
    envelope_to_json,
)
from gd_crypto.keyring import MasterKeyRing
from gd_crypto.password import hash_password, hmac_index, hmac_index_matches, verify_password
from gd_crypto.suites import current_suite, get_suite, SUITE_GM, SUITE_INTL

__all__ = [
    "MasterKeyRing", "current_suite", "get_suite", "SUITE_INTL", "SUITE_GM",
    "encrypt_envelope", "decrypt_envelope", "envelope_to_json", "envelope_from_json",
    "hash_password", "verify_password", "hmac_index", "hmac_index_matches",
    "issue_context", "verify_context", "renew_context",
    "compute_record_hash", "verify_record_hash",
]
