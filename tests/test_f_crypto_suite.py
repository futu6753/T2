# -*- coding: utf-8 -*-
"""
@file    test_f_crypto_suite.py
@brief   H09 §二 F 组验收:密码套件(intl 默认 / gm 冒烟 / 切换存量兼容 /
         13-R-IDP-2 双写窗口+断点续迁 / DEMO 正交 / profile 断言)。
         gm 原语纯 Python 参考实现锚定:SM3 双标准向量(GB/T 32905 附录 A)、
         SM4 标准向量(GB/T 32907;附录 100 万次迭代向量
         595298c7c6fd271f0402f804c33d3f66 已在开发期全量验证,CI 因时长仅保
         单块向量)、GCM 模式层与 cryptography AES-GCM 随机对拍、
         SM2 标准密钥对向量(GB/T 32918.5 附录 A.2)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os
import secrets
import tempfile
import unittest
from unittest import mock

from tests.base import TEST_KEY_HEX
from tests.idp_env import ADMIN_ACCOUNT, IdpEnv, TEST_IP, USER_ACCOUNT, USER_PASSWORD
from tests.cv_env import CvEnv, sample_cert_jpeg

from apps.idp.tokens import mint_id_token
from gd_crypto import decrypt_envelope, encrypt_envelope, envelope_from_json
from gd_crypto.keyring import MasterKeyRing
from gd_crypto.migrate import run_migration
from gd_crypto.suites import ALG_SM4_GCM, get_suite, SUITE_GM, SUITE_INTL
from gd_sso_client.jwt_verify import verify_jwt
from gd_storage.audit import verify_chain

GM_ENV = {"CRYPTO_SUITE": SUITE_GM}
PHONE_SAMPLE = "13800001111"


def _ring() -> MasterKeyRing:
    """@brief 测试主密钥环"""
    return MasterKeyRing.from_env({"MASTER_KEY_HEX": TEST_KEY_HEX,
                                   "MASTER_KEY_ID": "mk1"})


def _audit_actions(db) -> list:
    """@brief 审计事件序列"""
    return [row[0] for row in db.query("SELECT action FROM audit_logs ORDER BY id")]


class TestFGmPrimitives(unittest.TestCase):
    """F.1 前置:gm 原语标准向量与对拍锚定。"""

    def test_f_gm_sm3_standard_vectors(self):
        """SM3 双标准向量(GB/T 32905 附录 A.1/A.2)"""
        from gd_crypto.gm.sm3 import sm3_digest
        self.assertEqual(sm3_digest(b"abc").hex(),
                         "66c7f0f462eeedd9d1f2d46bdc10e4e2"
                         "4167c4875cf2f7a2297da02b8f4ba8e0")
        self.assertEqual(sm3_digest(b"abcd" * 16).hex(),
                         "debe9ff92275b8a138604889c18e5a4d"
                         "6fdb70e5387e5765293dcba39c0c5732")

    def test_f_gm_sm4_standard_vector(self):
        """SM4 标准单块向量(GB/T 32907 附录 A 例 1)"""
        from gd_crypto.gm.sm4 import encrypt_block, expand_key
        key = bytes.fromhex("0123456789abcdeffedcba9876543210")
        self.assertEqual(encrypt_block(expand_key(key), key).hex(),
                         "681edf34d206965e86b3e94f536e4246")

    def test_f_gm_gcm_crosscheck_aes(self):
        """GCM 模式层与 cryptography AES-GCM 逐字节对拍(含长 nonce 路径)"""
        from cryptography.hazmat.primitives.ciphers import algorithms, Cipher, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from gd_crypto.gm.gcm import gcm_decrypt, gcm_encrypt

        def aes_core(key: bytes):
            enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
            return lambda block: enc.update(block)

        for trial in range(8):
            key = secrets.token_bytes(32)
            nonce = secrets.token_bytes(12 if trial % 3 else 16)
            plaintext = secrets.token_bytes(trial * 37)
            aad = secrets.token_bytes(trial * 5)
            ct, tag = gcm_encrypt(aes_core(key), nonce, plaintext, aad)
            self.assertEqual(ct + tag, AESGCM(key).encrypt(nonce, plaintext, aad))
            self.assertEqual(gcm_decrypt(aes_core(key), nonce, ct, tag, aad),
                             plaintext)

    def test_f_gm_sm2_standard_keypair_and_roundtrip(self):
        """SM2 标准私钥→公钥向量(GB/T 32918.5 附录 A.2)+ 签验回环/篡改拒绝"""
        from gd_crypto.gm import sm2
        d = 0x3945208F7B2144B13F36E38AC6D39F95889393692860B51A42FB81EF4DF7C5B8
        expected_x = 0x09F9DF311E5421A150DD7D161E4BC5C672179FAD1833FC076BB08FF356F35020
        expected_y = 0xCCEA490CE26775A52DC6EA718CC1AA600AED05FBF35E084A6632F6072DA9AD13
        px, py = sm2.public_key_from_private(d)
        self.assertEqual(px, expected_x)
        self.assertEqual(py, expected_y)
        signature = sm2.sign(b"m10", d)
        self.assertTrue(sm2.verify(b"m10", signature, (px, py)))
        self.assertFalse(sm2.verify(b"m1O", signature, (px, py)))
        tampered = signature[:-1] + bytes([signature[-1] ^ 1])
        self.assertFalse(sm2.verify(b"m10", tampered, (px, py)))


class TestF1GmSmoke(unittest.TestCase):
    """F.1:CRYPTO_SUITE=gm 冒烟——登录、发证/溯源、审计链校验。"""

    def test_f1_gm_smoke_login_issue_trace_audit_chain(self):
        """gm 套件端到端:本地登录→上传→发证→溯源命中→链校验全绿"""
        env = CvEnv(extra_environ=GM_ENV)
        try:
            self.assertEqual(env.ctx.suite.name, SUITE_GM)
            client = env.client()
            token = env.register_and_login(client, "gm_user", "Gm!Passw0rd88")
            cert_id = env.upload_cert(client, token)
            issued = env.issue(client, token, cert_id)
            self.assertEqual(issued.status_code, 200, issued.body)
            import base64
            image = base64.b64decode(issued.json()["image_b64"])
            traced = env.trace(client, token, image)
            self.assertEqual(traced.status_code, 200, traced.body)
            self.assertTrue(traced.json().get("found"), traced.body)
            # 存量信封确为国密算法(不降级)
            rows = env.db.query("SELECT blob_path FROM cv_certs LIMIT 1")
            blob_file = os.path.join(env.ctx.store._blob_dir, rows[0][0])
            with open(blob_file, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["alg"], ALG_SM4_GCM)
            self.assertGreater(verify_chain(env.db), 0)
        finally:
            env.close()


class TestF2SwitchStockCompat(unittest.TestCase):
    """F.2:intl 写入 → 显式切 gm → 存量兼容全断言。"""

    def test_f2_switch_intl_to_gm_stock_compat(self):
        """存量可解/口令可验+透明重哈希/旧 id_token 按 kid 验签/切换锚点+链全绿"""
        env = IdpEnv()
        env.seed_admin_and_user()
        ctx = env.ctx
        ctx.accounts.create_user("switch_u", "切换用户", USER_PASSWORD,
                                 ctx.profile, "system", TEST_IP,
                                 phone=PHONE_SAMPLE, force_change=False)
        user = ctx.accounts.get_user("switch_u")
        old_hash = user["password_hash"]
        self.assertTrue(old_hash.startswith("$argon2id$"))
        old_token = mint_id_token(ctx.issuer, "quiz",
                                  {"account": "switch_u", "display_name": "切换用户"},
                                  ["users"], ["pwd"], "n1", ctx.keys,
                                  suite=ctx.suite)
        # 显式切换到 gm 并"重启"
        env.extra_environ = dict(GM_ENV)
        env.restart()
        ctx = env.ctx
        self.assertEqual(ctx.suite.name, SUITE_GM)
        # 1) 存量手机号密文(intl 信封)在 gm 生效期仍可解
        row = ctx.db.query("SELECT phone_ct FROM idp_users WHERE account = ?",
                           ("switch_u",))[0]
        plain = decrypt_envelope(envelope_from_json(row[0]), ctx.ring)
        self.assertEqual(plain.decode(), PHONE_SAMPLE)
        # 2) 口令可验并触发透明重哈希为 pbkdf2-sm3
        outcome, _ = ctx.accounts.password_login_step(
            "switch_u", USER_PASSWORD, ctx.profile, TEST_IP)
        self.assertNotEqual(outcome, "failed")
        new_hash = ctx.accounts.get_user("switch_u")["password_hash"]
        self.assertTrue(new_hash.startswith("pbkdf2-sm3$"), new_hash)
        # 3) 切换前签发的 RS256 id_token 按 kid 验签通过(JWKS 双钥)
        claims = verify_jwt(old_token, ctx.keys.jwks(), ctx.issuer, "quiz",
                            nonce="n1")
        self.assertEqual(claims["sub"], "switch_u")
        # 4) crypto_suite_changed 锚点存在且链校验全绿(逐条 alg 混链)
        self.assertIn("crypto_suite_changed", _audit_actions(ctx.db))
        self.assertGreater(verify_chain(ctx.db), 0)
        env.close()


class TestRIdp2Migrate(unittest.TestCase):
    """F.3 / K.2 test_r_idp2_migrate:迁移幂等+双写窗口+断点续迁+审计锚点。"""

    def _seed(self, env: IdpEnv, count: int):
        """@brief 播种含手机号的用户(信封+HMAC 索引对象)"""
        for i in range(count):
            env.ctx.accounts.create_user(f"mig{i}", f"迁移用户{i}", USER_PASSWORD,
                                         env.ctx.profile, "system", TEST_IP,
                                         phone=f"1390000{i:04d}",
                                         force_change=False)

    def test_r_idp2_migrate(self):
        """双写窗口新旧套件均可解;中断后断点续迁与一次性迁移结果一致(幂等)"""
        env = IdpEnv()
        env.seed_admin_and_user()
        self._seed(env, 5)
        ctx = env.ctx
        ring = ctx.ring
        # --- 双写窗口:窗口内新写入信封含 dual 段,两套件段均独立可解可验 ---
        dual_env = {"CRYPTO_DUAL_WRITE": SUITE_GM}
        envelope = encrypt_envelope(PHONE_SAMPLE.encode(), ring,
                                    get_suite(SUITE_INTL), environ=dual_env)
        self.assertIn("dual", envelope)
        self.assertEqual(envelope["dual"]["alg"], ALG_SM4_GCM)
        self.assertEqual(decrypt_envelope(envelope, ring).decode(), PHONE_SAMPLE)
        self.assertEqual(decrypt_envelope(envelope["dual"], ring).decode(),
                         PHONE_SAMPLE)
        # 主段损毁(模拟旧套件不可用)时 dual 段承接可用性
        broken = dict(envelope)
        broken["tag"] = envelope["dual"]["tag"]
        self.assertEqual(decrypt_envelope(broken, ring).decode(), PHONE_SAMPLE)
        # --- 断点续迁:第二批进度后注入中断,同一状态文件续跑 ---
        state_file = tempfile.mktemp(suffix=".json")
        target = get_suite(SUITE_GM)

        class _Interrupt(Exception):
            """迁移中断注入信号"""

        progress_seen = {"count": 0}
        real_append = ctx.audit.append

        def _flaky_append(actor, action, detail, ip):
            result = real_append(actor, action, detail, ip)
            if action == "crypto_migration_progress":
                progress_seen["count"] += 1
                if progress_seen["count"] == 2:
                    raise _Interrupt()
            return result

        with mock.patch("gd_crypto.migrate.PROGRESS_BATCH", 2):
            with mock.patch.object(ctx.audit, "append", side_effect=_flaky_append):
                with self.assertRaises(_Interrupt):
                    run_migration(ctx.db, ring, target, ctx.audit,
                                  state_file=state_file)
            report = run_migration(ctx.db, ring, target, ctx.audit,
                                   state_file=state_file)
        # 结果与一次性迁移一致:全部信封 alg=SM4-GCM 且可解,索引重算为 HMAC-SM3
        rows = ctx.db.query("SELECT account, phone_ct, phone_index FROM idp_users"
                            " WHERE phone_ct IS NOT NULL")
        self.assertEqual(len(rows), 5)
        for account, phone_ct, phone_index in rows:
            parsed = envelope_from_json(phone_ct)
            self.assertEqual(parsed["alg"], ALG_SM4_GCM, account)
            self.assertNotIn("dual", parsed)
            self.assertTrue(decrypt_envelope(parsed, ring).decode()
                            .startswith("139"))
            self.assertTrue(phone_index.startswith("HMAC-SM3$"))
        # 审计锚点:开始 ×1(续迁不重复写 started)/进度 ≥2/完成 存在
        actions = _audit_actions(ctx.db)
        self.assertEqual(actions.count("crypto_migration_started"), 1)
        self.assertGreaterEqual(actions.count("crypto_migration_progress"), 2)
        self.assertIn("crypto_migration_completed", actions)
        self.assertGreaterEqual(
            sum(c["migrated"] for c in report["phases"].values()), 1)
        # 幂等:新一轮全量重跑零迁移,链校验全绿
        rerun = run_migration(ctx.db, ring, target, ctx.audit,
                              state_file=tempfile.mktemp(suffix=".json"))
        self.assertEqual(sum(c["migrated"] for c in rerun["phases"].values()), 0)
        self.assertGreater(verify_chain(ctx.db), 0)
        env.close()


class TestMasterKeyRotation(unittest.TestCase):
    """H06-E10 轮换=迁移:旧 kid 对象全部重包为新钥,算法不变,幂等。"""

    def test_e10_rotate_master_key_rewrap_idempotent(self):
        """双钥环轮换:重包计数/新 kid/明文不变/重跑零重包/审计锚点"""
        from gd_crypto.migrate import run_key_rotation
        env = IdpEnv()
        env.seed_admin_and_user()
        env.ctx.accounts.create_user("rot_u", "轮换用户", USER_PASSWORD,
                                     env.ctx.profile, "system", TEST_IP,
                                     phone=PHONE_SAMPLE, force_change=False)
        old_ring = env.ctx.ring
        new_key_hex = secrets.token_hex(32)
        rotated_ring = MasterKeyRing.from_env({
            "MASTER_KEY_HEX": new_key_hex, "MASTER_KEY_ID": "mk2",
            "OLD_MASTER_KEY_HEX": TEST_KEY_HEX, "OLD_MASTER_KEY_ID": "mk1"})
        report = run_key_rotation(env.ctx.db, rotated_ring, env.ctx.audit)
        self.assertGreaterEqual(report["rewrapped"], 1)
        row = env.ctx.db.query("SELECT phone_ct FROM idp_users WHERE account = ?",
                               ("rot_u",))[0]
        parsed = envelope_from_json(row[0])
        self.assertEqual(parsed["wrapped_dek"]["kid"], "mk2")
        self.assertEqual(parsed["alg"], "AES-256-GCM")  # 算法不变
        self.assertEqual(decrypt_envelope(parsed, rotated_ring).decode(),
                         PHONE_SAMPLE)
        rerun = run_key_rotation(env.ctx.db, rotated_ring, env.ctx.audit)
        self.assertEqual(rerun["rewrapped"], 0)
        self.assertIn("master_key_rotated", _audit_actions(env.ctx.db))
        self.assertGreater(verify_chain(env.ctx.db), 0)
        # 旧环(无新钥)对已轮换对象给出 E10 明确指引
        with self.assertRaises(Exception):
            decrypt_envelope(parsed, old_ring)
        env.close()


class TestF4DemoOrthogonal(unittest.TestCase):
    """F.4:DEMO=1 下套件行为与生产一致(算法不降级)。"""

    def test_f4_demo_orthogonal_gm_not_downgraded(self):
        """DEMO 模式 + gm:新写入信封仍为 SM4-GCM"""
        env = IdpEnv(is_demo=True, extra_environ=GM_ENV)
        env.seed_admin_and_user()
        env.ctx.accounts.create_user("demo_gm", "演示用户", USER_PASSWORD,
                                     env.ctx.profile, "system", TEST_IP,
                                     phone=PHONE_SAMPLE, force_change=False)
        row = env.ctx.db.query("SELECT phone_ct FROM idp_users WHERE account = ?",
                               ("demo_gm",))[0]
        self.assertEqual(envelope_from_json(row[0])["alg"], ALG_SM4_GCM)
        env.close()


class TestF5ProfileAssertions(unittest.TestCase):
    """F.5:默认 intl 的 healthz 断言与 gm 显式启用三联(日志/审计/selfcheck)。"""

    def test_f5_default_profile_healthz_reports_intl(self):
        """不配置 CRYPTO_SUITE 起服务 → /healthz 报 intl(默认即国际套件)"""
        env = IdpEnv()
        resp = env.client().get("/healthz")
        self.assertEqual(resp.json()["crypto_suite"], SUITE_INTL)
        self.assertNotIn("crypto_suite_changed", _audit_actions(env.ctx.db))
        env.close()

    def test_f5_gm_startup_banner_audit_and_selfcheck(self):
        """显式 gm 起服务 → 大写提示日志 + crypto_suite_changed 审计 + 上报正确"""
        with self.assertLogs("gd_storage.suite_state", level="WARNING") as logs:
            env = IdpEnv(extra_environ=GM_ENV)
        self.assertTrue(any("CRYPTO_SUITE=GM" in line for line in logs.output))
        self.assertIn("crypto_suite_changed", _audit_actions(env.ctx.db))
        self.assertEqual(env.client().get("/healthz").json()["crypto_suite"],
                         SUITE_GM)
        # 重启同套件不重复写切换事件
        env.restart()
        self.assertEqual(_audit_actions(env.ctx.db).count("crypto_suite_changed"), 1)
        env.close()


if __name__ == "__main__":
    unittest.main()
