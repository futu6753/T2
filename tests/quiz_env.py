# -*- coding: utf-8 -*-
"""
@file    quiz_env.py
@brief   quiz 测试基座:IdP + quiz 进程内装配;提供 SSO 已登录客户端与
         游客客户端(自动领 5 位 ID),以及 JSON 便捷请求。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json

from tests.idp_env import IdpEnv
from tests.rp_env import drive_sso_login, make_sso_client

from apps.quiz.web import create_app as create_quiz
from selfcheck.asgi import AsgiClient

QUIZ_REDIRECT = "http://quiz.gd.test/sso/callback"


def jbody(client, path, payload, method="POST"):
    """@brief JSON 请求便捷封装"""
    return client.request(method, path,
                          raw_body=json.dumps(payload).encode(),
                          content_type="application/json")


class QuizEnv:
    """IdP + quiz 双系统装配。"""

    def __init__(self, guest_mode_enabled: bool = True):
        """@brief 建 IdP、注册 quiz 客户端、装配应用(装配即 seed 题库)"""
        self.idp = IdpEnv()
        self.idp.seed_admin_and_user()
        self.sso, self.rp_store, self.secret = make_sso_client(
            self.idp, "quiz", "quiz-m7", QUIZ_REDIRECT)
        self.app = create_quiz(self.idp.ctx.db, self.idp.ctx.suite, self.sso,
                               guest_mode_enabled=guest_mode_enabled)
        self.db = self.idp.ctx.db

    def client(self) -> AsgiClient:
        """@brief 匿名客户端"""
        return AsgiClient(self.app)

    def sso_client(self, account: str = None, password: str = None):
        """@brief SSO 已登录客户端(默认 alice → owner "sso:alice")"""
        client = self.client()
        kwargs = {}
        if account:
            kwargs = {"account": account, "password": password}
        drive_sso_login(self.idp, client, next_path="/", **kwargs)
        return client

    def guest_client(self) -> tuple:
        """@brief 游客客户端 @return (client, guest_code)"""
        client = self.client()
        resp = client.request("POST", "/guest/new")
        assert resp.status_code == 200, resp.body
        return client, resp.json()["guest_code"]
