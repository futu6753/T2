# -*- coding: utf-8 -*-
"""
@file    mode.py
@brief   运行模式切换服务(H05 §3):DEMO→生产自动恢复清单(顺序执行、幂等)、
         生产→DEMO 二次确认+原因入审计、生产前置条件校验(fail-closed)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_common.errors import ConfigError, PolicyValidationError
from gd_policy.profile import MODE_DEMO, MODE_PROD
from gd_storage import events
from selfcheck.registry import PHASE_PROD, run_profile_assertions

DEMO_MODE_SETTING_KEY = "demo_mode"


class ModeService:
    """DEMO ⇄ 等保三生产 状态机(唯一切换入口)。"""

    def __init__(self, ctx):
        """@brief 绑定应用上下文"""
        self._ctx = ctx

    def _run_http_selfcheck(self) -> list:
        """@brief 进程内执行 http 级 D4/D5 断言(与 selfcheck_prod 同源 DSL)"""
        from apps.idp.web import create_app          # 函数内导入避免模块级循环依赖
        from selfcheck.asgi import AsgiClient
        from selfcheck.registry import run_http_assertions
        client = AsgiClient(create_app(self._ctx))
        _, failures = run_http_assertions(client, PHASE_PROD)
        return failures

    def _check_prod_preconditions(self):
        """@brief 生产前置条件(不满足即切换失败并明示原因,H05 §3.2.5)"""
        if self._ctx.ring.is_demo_key():
            raise ConfigError("主密钥为演示派生默认值,禁止进入生产模式")

    def switch_to_prod(self, actor: str, ip: str) -> dict:
        """
        @brief  DEMO→生产自动恢复清单(顺序执行、幂等,H05 §3.2)
        @return 恢复报告(供 /healthz 详情与日志)
        """
        self._check_prod_preconditions()
        previous_mode = self._ctx.profile.mode
        # 1. 落覆盖并重解析 SecurityProfile:D2–D8 立即失效
        self._ctx.settings.set_override(DEMO_MODE_SETTING_KEY, False, actor, ip,
                                        audit_writer=self._ctx.audit)
        profile = self._ctx.refresh_profile()
        # 3. 演示账号自动停用(不删、留审计)
        disabled_count = self._ctx.accounts.disable_demo_accounts(ip)
        # 4. amr 含 demo 的会话全部吊销
        revoked_count = self._ctx.sessions.revoke_demo_sessions()
        # 6. 审计锚点
        self._ctx.audit.append(actor, events.MODE_CHANGED,
                               {"from": previous_mode, "to": MODE_PROD}, ip)
        # 7. 开机自检(fail-closed:任何一项失败即抛错,禁止降级运行)
        _, failures, _ = run_profile_assertions(profile, PHASE_PROD)
        failures += self._run_http_selfcheck()
        if failures:
            raise ConfigError(f"生产自检失败: {[f['id'] for f in failures]}")
        return {"mode": profile.mode, "demo_accounts_disabled": disabled_count,
                "demo_sessions_revoked": revoked_count, "selfcheck": "PASS"}

    def switch_to_demo(self, actor: str, ip: str, confirm: bool,
                       reason: str) -> dict:
        """@brief 生产→DEMO:二次确认+原因强制入审计(H05 §3.3)"""
        if not confirm or not (reason or "").strip():
            raise PolicyValidationError("切入 DEMO 必须二次确认并填写原因")
        _, source = self._ctx.settings.get_with_source(DEMO_MODE_SETTING_KEY)
        if source == "env":
            raise PolicyValidationError("运行模式已由环境变量锁定,请修改 env 并重启")
        previous_mode = self._ctx.profile.mode
        self._ctx.settings.set_override(DEMO_MODE_SETTING_KEY, True, actor, ip,
                                        audit_writer=self._ctx.audit)
        profile = self._ctx.refresh_profile()
        # 演示账号重新启用并播种;不改动任何生产数据(H05 §3.3)
        self._ctx.accounts.seed_demo_accounts(profile, ip)
        self._ctx.audit.append(actor, events.MODE_CHANGED,
                               {"from": previous_mode, "to": MODE_DEMO,
                                "reason": reason}, ip)
        return {"mode": profile.mode}
