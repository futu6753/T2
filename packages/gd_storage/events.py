# -*- coding: utf-8 -*-
"""
@file    events.py
@brief   统一审计事件字典(H04 §三.a 为唯一清单来源;新增事件 MUST 先入 H04 表)。
         失败独立 action;全平台各子系统 MUST 引用本字典,禁止散落魔法字符串。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""

# ---- 登录与账户 ----
LOGIN_SUCCESS = "login_success"                  # 登录成功
LOGIN_FAILED = "login_failed"                    # 登录失败(独立 action)
LOGIN_LOCKED = "login_locked"                    # 触发账号锁定
LOGIN_DENIED_DISABLED = "login_denied_disabled"  # 停用账户被拦截
LOGIN_DENIED_NETWORK = "login_denied_network"    # admin_networks 网段拦截
USER_CREATED = "user_created"                    # 建号
USER_DISABLED = "user_disabled"                  # 停用
USER_ENABLED = "user_enabled"                    # 启用
USER_DELETED = "user_deleted"                    # 删除账户
USER_UNLOCKED = "user_unlocked"                  # 管理员/CLI 解锁
PASSWORD_CHANGED = "password_changed"            # 改密
PASSWORD_RESET = "password_reset"                # 管理员重置口令
TWOFA_ENABLED = "twofa_enabled"                  # 开启双因素
TWOFA_DISABLED = "twofa_disabled"                # 关闭双因素
TWOFA_RESET = "twofa_reset"                      # 重置双因素
SSO_ACCOUNT_LINKED = "sso_account_linked"        # 首次 SSO 建号/映射

# ---- 业务对象 ----
OBJECT_UPLOADED = "object_uploaded"              # 上传(证件等)
OBJECT_DELETED = "object_deleted"                # 删除对象(连带密文 blob)
CERT_ISSUED = "cert_issued"                      # 生成水印件(发证)
CERT_TRACED = "cert_traced"                      # 溯源识别
RECORD_REVOKED = "record_revoked"                # 备案撤销(13-R-CV-5,v2.0 新增)
DATA_EXPORTED = "data_exported"                  # 导出(审计 CSV / /me/export)

# ---- 平台与策略 ----
SETTINGS_CHANGED = "settings_changed"            # 策略修改(旧值→新值;secret 只记"已修改")
MODE_CHANGED = "mode_changed"                    # 运行模式切换(H05 §3.2.6)
MODE_DEMO_HEARTBEAT = "mode_demo_heartbeat"      # DEMO 态每小时审计心跳(H05 §2)
CRYPTO_SUITE_CHANGED = "crypto_suite_changed"    # 密码套件切换锚点(H04 §8.2,v2.0 新增)
CRYPTO_MIGRATION_STARTED = "crypto_migration_started"      # 套件迁移开始(13-R-IDP-2)
CRYPTO_MIGRATION_PROGRESS = "crypto_migration_progress"    # 套件迁移进度批次(13-R-IDP-2)
CRYPTO_MIGRATION_COMPLETED = "crypto_migration_completed"  # 套件迁移完成(13-R-IDP-2)
AI_ACTION_EXECUTED = "ai_action_executed"        # AI 动作执行/dry-run/回滚(13-R-F3D-2,v2.0 新增)
MIGRATION_PROGRESS = "migration_progress"        # 套件迁移进度锚点(13-R-IDP-2,v2.0 新增)

# 全量字典(自动化测试断言事件覆盖 ≥20 类,H09 §二 A.4)
ALL_EVENTS = tuple(
    value for name, value in sorted(globals().items())
    if name.isupper() and isinstance(value, str) and name != "ALL_EVENTS"
)
