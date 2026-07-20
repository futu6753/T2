# -*- coding: utf-8 -*-
"""
@file    schema.py
@brief   统一设置服务 SETTINGS_SCHEMA(H03 为唯一来源,MUST NOT 在代码中另行硬编码副本)。
         每参数:类型/默认值/范围/等保下限校验/来源 env 名/是否需重启/是否 secret。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import dataclasses
from typing import Callable, Optional

TYPE_INT = "int"
TYPE_FLOAT = "float"          # v2.0 M6:fps 阈值/告警延时分钟允许小数(L03 §3)
TYPE_BOOL = "bool"
TYPE_STR = "str"
TYPE_CHOICE = "choice"

SECTION_AUTH = "安全·登录认证"
SECTION_SESSION = "会话"
SECTION_METHODS = "登录方式"
SECTION_ACCESS = "访问控制"
SECTION_AUDIT = "审计"
SECTION_STORAGE = "存储"
SECTION_MODE = "模式"
SECTION_NVR_PATROL = "NVR·巡检"
SECTION_NVR_ALERT = "NVR·告警"
SECTION_NVR_REPORT = "NVR·运维报告"
SECTION_NVR_METRICS = "NVR·指标"
SECTION_F3D_DATA = "F3D·连接与采集"
SECTION_F3D_ALARM = "F3D·告警策略"
SECTION_F3D_RENDER = "F3D·渲染降级"
SECTION_F3D_ASSIST = "F3D·AI 助手"

COMPLEXITY_THREE = "three_classes"     # 大小写/数字/符号至少三类
COMPLEXITY_FOUR = "four_classes"
ROLE_AUDITOR = "auditor"
ROLE_USER = "user"

# 等保下限常量(H03 §2/§3 / H04 §三;禁魔法数字)
FLOOR_PASSWORD_MIN_LENGTH = 8
FLOOR_MAX_LOGIN_FAILURES = 5
FLOOR_LOCKOUT_MINUTES = 15
FLOOR_PASSWORD_MAX_AGE_DAYS = 90
FLOOR_AUDIT_RETENTION_DAYS = 180
AUDIT_RETENTION_PERMANENT = 0          # 0 = 永久留存


@dataclasses.dataclass(frozen=True)
class Param:
    """单个策略参数的元数据(schema 驱动渲染与校验的唯一载体,H03 §8)。"""

    key: str
    label: str
    section: str
    type: str
    default: object
    help: str = ""
    ge: Optional[int] = None
    le: Optional[int] = None
    choices: tuple = ()
    unit: str = ""
    restart: bool = False        # 需重启生效项(界面明示,H03 §8)
    secret: bool = False         # secret 类修改只审计"已修改"(H03 §8)
    env: Optional[str] = None    # 环境变量名,缺省为 key 大写
    prod_check: Optional[Callable] = None   # 等保下限校验:返回 None=通过,str=违规原因

    def env_name(self) -> str:
        """@brief 环境变量名(优先显式声明,否则 key 大写)"""
        return self.env or self.key.upper()


def _check_max_age_prod(value: int) -> Optional[str]:
    """@brief 生产禁 0(必须强制定期更换)且 ≤90 天(H03 §2)"""
    if value == 0:
        return "生产模式禁止关闭口令定期更换(0),等保下限 ≤90 天"
    if value > FLOOR_PASSWORD_MAX_AGE_DAYS:
        return f"口令最长使用期生产上限 {FLOOR_PASSWORD_MAX_AGE_DAYS} 天"
    return None


def _check_retention_prod(value: int) -> Optional[str]:
    """@brief 审计留存 ≥180 天(0=永久 允许)(H04 §三.c)"""
    if value != AUDIT_RETENTION_PERMANENT and value < FLOOR_AUDIT_RETENTION_DAYS:
        return f"审计留存生产下限 {FLOOR_AUDIT_RETENTION_DAYS} 天(0=永久)"
    return None


SETTINGS_SCHEMA: tuple = (
    # ---- 模式(仅 SecurityProfile 解析器可读取,业务代码禁读,H05 §1.2) ----
    Param("demo_mode", "演示模式", SECTION_MODE, TYPE_BOOL, False, env="DEMO_MODE",
          help="0=生产(默认,不配置即安全);1=演示。生产→DEMO 须二次确认并审计"),
    # ---- 口令与鉴别信息(H03 §2) ----
    Param("password_min_length", "口令最小长度", SECTION_AUTH, TYPE_INT, 10,
          ge=FLOOR_PASSWORD_MIN_LENGTH, le=64, unit="字符",
          help=f"等保下限 ≥{FLOOR_PASSWORD_MIN_LENGTH}"),
    Param("password_complexity", "口令复杂度", SECTION_AUTH, TYPE_CHOICE, COMPLEXITY_THREE,
          choices=(COMPLEXITY_THREE, COMPLEXITY_FOUR), help="等保下限:至少三类"),
    Param("password_max_age_days", "口令最长使用期", SECTION_AUTH, TYPE_INT,
          FLOOR_PASSWORD_MAX_AGE_DAYS, ge=0, le=3650, unit="天",
          prod_check=_check_max_age_prod, help="0=不强制(仅 DEMO);生产 ≤90 且禁 0"),
    Param("first_login_force_change", "首登强制改密", SECTION_AUTH, TYPE_BOOL, True,
          prod_check=lambda v: None if v else "生产模式首登强改密不可关闭",
          help="管理员建号/重置后首登强改"),
    Param("password_history", "口令历史禁复用", SECTION_AUTH, TYPE_INT, 3, ge=0, le=24,
          unit="代", help="新系统补齐项,建议 ≥3"),
    Param("max_login_failures", "登录失败锁定阈值", SECTION_AUTH, TYPE_INT,
          FLOOR_MAX_LOGIN_FAILURES, ge=3, le=10, unit="次",
          prod_check=lambda v: None if v <= FLOOR_MAX_LOGIN_FAILURES
          else f"生产上限 {FLOOR_MAX_LOGIN_FAILURES} 次", help="含两步验证失败计次"),
    Param("lockout_minutes", "锁定时长", SECTION_AUTH, TYPE_INT, FLOOR_LOCKOUT_MINUTES,
          ge=1, le=1440, unit="分钟",
          prod_check=lambda v: None if v >= FLOOR_LOCKOUT_MINUTES
          else f"生产下限 {FLOOR_LOCKOUT_MINUTES} 分钟", help="到期自动解锁,管理员可立即解锁"),
    Param("login_rate_cap_per_minute", "登录限速(IP/分钟)", SECTION_AUTH, TYPE_INT, 40,
          ge=1, le=6000, unit="次", help="应用层限速;DEMO 阈值 ×10(05-D8)"),
    # ---- 会话(H03 §3) ----
    Param("session_idle_minutes", "会话空闲超时", SECTION_SESSION, TYPE_INT, 30,
          ge=1, le=1440, unit="分钟", help="无操作自动退出(等保必须)"),
    Param("session_max_hours", "会话绝对有效期", SECTION_SESSION, TYPE_INT, 12,
          ge=1, le=168, unit="小时"),
    Param("sso_session_ttl_seconds", "RP 本地会话 TTL", SECTION_SESSION, TYPE_INT, 28800,
          ge=60, le=604800, unit="秒", env="SSO_SESSION_TTL"),
    # ---- 登录方式开关(H03 §4,保存即热生效) ----
    Param("method_password", "口令登录", SECTION_METHODS, TYPE_BOOL, True,
          help="关闭前保存校验防全员自锁"),
    Param("method_totp_only", "TOTP 直登", SECTION_METHODS, TYPE_BOOL, False,
          help="单因素,界面标注谨慎"),
    Param("method_sms", "短信验证码登录", SECTION_METHODS, TYPE_BOOL, False),
    Param("method_webauthn", "安全密钥(WebAuthn)", SECTION_METHODS, TYPE_BOOL, False),
    Param("method_client_cert", "数字证书(mTLS)", SECTION_METHODS, TYPE_BOOL, False,
          restart=True, help="需 CA 文件 + HTTPS,重启生效"),
    Param("method_wechat", "微信扫码", SECTION_METHODS, TYPE_BOOL, False,
          help="生产配 APPID/SECRET 走真实流程;未配置时登录页自动隐藏"),
    Param("require_totp", "强制全员双因素", SECTION_METHODS, TYPE_BOOL, False,
          env="ENFORCE_2FA", help="生产建议开启"),
    Param("sso_satisfies_2fa", "SSO 会话视同已双因素", SECTION_METHODS, TYPE_BOOL, True,
          env="SSO_SATISFIES_2FA"),
    # ---- 访问控制(H03 §5) ----
    Param("allow_open_register", "开放注册", SECTION_ACCESS, TYPE_BOOL, False,
          help="默认关:账户由管理员分配"),
    Param("admin_networks", "管理终端网段白名单", SECTION_ACCESS, TYPE_STR, "",
          help="CIDR 逗号分隔;空=不限制(生产建议配置);保存时防自锁校验"),
    Param("sso_default_role", "SSO 建号默认角色", SECTION_ACCESS, TYPE_CHOICE, ROLE_AUDITOR,
          choices=(ROLE_AUDITOR, ROLE_USER), env="SSO_DEFAULT_ROLE", help="最小权限"),
    Param("quiz_guest_mode", "刷题游客模式", SECTION_ACCESS, TYPE_BOOL, True,
          help="5 位 ID 游客通道;业主要求全站实名则关闭(H03 §6)"),
    # ---- 审计(H04 §三) ----
    Param("audit_retention_days", "审计留存期", SECTION_AUDIT, TYPE_INT,
          FLOOR_AUDIT_RETENTION_DAYS, ge=0, le=36500, unit="天",
          prod_check=_check_retention_prod, help="生产 ≥180;0=永久"),
    # ---- 存储(H12) ----
    Param("db_pool_size", "PG 连接池上限/实例", SECTION_STORAGE, TYPE_INT, 10,
          ge=1, le=100),
    # ---- P2 功能开关(H13,默认关,关闭态零行为差异) ----
    Param("risk_engine_enabled", "风险自适应认证(P2)", SECTION_AUTH, TYPE_BOOL, False,
          help="13-R-IDP-1;关闭时登录行为与基线完全一致"),
    Param("adaptive_patrol_enabled", "自适应巡检(P2)", SECTION_ACCESS, TYPE_BOOL, False,
          help="13-R-NVR-2;关闭时与固定间隔行为完全一致"),
    # ---- NVR 监控(L04 §6 设置页可编辑清单,平台化并入统一策略层) ----
    Param("nvr_patrol_enabled", "启用定时巡检", SECTION_NVR_PATROL, TYPE_BOOL, True,
          env="NVRM_MONITOR__ENABLED"),
    Param("nvr_patrol_interval_seconds", "巡检间隔", SECTION_NVR_PATROL, TYPE_INT,
          120, ge=10, le=86400, unit="秒", env="NVRM_MONITOR__INTERVAL_SECONDS"),
    Param("nvr_patrol_concurrency", "并发上限", SECTION_NVR_PATROL, TYPE_INT, 10,
          ge=1, le=100, env="NVRM_MONITOR__CONCURRENCY"),
    Param("nvr_patrol_timeout_seconds", "单台超时", SECTION_NVR_PATROL, TYPE_INT,
          8, ge=1, le=120, unit="秒", env="NVRM_MONITOR__TIMEOUT_SECONDS"),
    Param("nvr_icmp_enabled", "ICMP 兜底探测", SECTION_NVR_PATROL, TYPE_BOOL, True,
          env="NVRM_MONITOR__ICMP_ENABLED", help="容器受限时关闭"),
    Param("nvr_channel_check_enabled", "通道巡检", SECTION_NVR_PATROL, TYPE_BOOL,
          True, env="NVRM_MONITOR__CHANNEL_CHECK_ENABLED"),
    Param("nvr_channel_offline_abnormal", "通道离线连带录像机异常",
          SECTION_NVR_PATROL, TYPE_BOOL, False,
          env="NVRM_MONITOR__CHANNEL_OFFLINE_ABNORMAL", help="T13 默认关"),
    Param("nvr_retention_days", "巡检明细保留", SECTION_NVR_PATROL, TYPE_INT, 90,
          ge=0, le=36500, unit="天", env="NVRM_MONITOR__RETENTION_DAYS",
          help="0=永久;时间线不清理"),
    Param("nvr_debounce_mode", "去抖模式", SECTION_NVR_ALERT, TYPE_CHOICE,
          "consecutive_failures",
          choices=("consecutive_failures", "offline_duration", "ewma",
                   "hysteresis", "adaptive"),
          env="NVRM_ALERTING__DEBOUNCE__MODE", help="13-R-NVR-1 五模式"),
    Param("nvr_consecutive_failures", "连续失败阈值", SECTION_NVR_ALERT,
          TYPE_INT, 3, ge=1, le=20,
          env="NVRM_ALERTING__DEBOUNCE__CONSECUTIVE_FAILURES"),
    Param("nvr_offline_duration_seconds", "持续故障阈值", SECTION_NVR_ALERT,
          TYPE_INT, 300, ge=30, le=86400, unit="秒",
          env="NVRM_ALERTING__DEBOUNCE__OFFLINE_DURATION_SECONDS"),
    Param("nvr_channel_alerts", "通道离线独立告警", SECTION_NVR_ALERT,
          TYPE_BOOL, True, env="NVRM_ALERTING__CHANNEL_ALERTS"),
    Param("nvr_recovery_notice", "恢复通知", SECTION_NVR_ALERT, TYPE_BOOL, True,
          env="NVRM_ALERTING__RECOVERY_NOTICE"),
    Param("nvr_retry_max_attempts", "通知重试次数", SECTION_NVR_ALERT, TYPE_INT,
          3, ge=1, le=10, env="NVRM_ALERTING__RETRY__MAX_ATTEMPTS",
          help="含首发"),
    Param("nvr_retry_backoff_seconds", "重试退避", SECTION_NVR_ALERT, TYPE_INT,
          60, ge=1, le=3600, unit="秒",
          env="NVRM_ALERTING__RETRY__BACKOFF_SECONDS", help="线性退避×次数"),
    Param("nvr_report_enabled", "定时生成周报", SECTION_NVR_REPORT, TYPE_BOOL,
          False, env="NVRM_WEEKLY_REPORT__ENABLED"),
    Param("nvr_report_cron", "生成时间 cron(UTC)", SECTION_NVR_REPORT,
          TYPE_STR, "0 9 * * 1", env="NVRM_WEEKLY_REPORT__CRON",
          prod_check=None, help="5 段;保存时校验(L04 §6)"),
    Param("nvr_report_period_days", "覆盖天数", SECTION_NVR_REPORT, TYPE_INT, 7,
          ge=1, le=90, unit="天", env="NVRM_WEEKLY_REPORT__PERIOD_DAYS"),
    Param("nvr_report_model", "Claude 模型", SECTION_NVR_REPORT, TYPE_STR,
          "claude-sonnet-4-6", env="NVRM_WEEKLY_REPORT__MODEL"),
    Param("nvr_metrics_per_device", "每设备指标", SECTION_NVR_METRICS,
          TYPE_BOOL, True, env="NVRM_METRICS__PER_DEVICE"),
    Param("nvr_metrics_per_channel", "每通道指标", SECTION_NVR_METRICS,
          TYPE_BOOL, True, env="NVRM_METRICS__PER_CHANNEL"),
    Param("nvr_metrics_include_disabled", "包含已停用设备", SECTION_NVR_METRICS,
          TYPE_BOOL, False, env="NVRM_METRICS__INCLUDE_DISABLED"),
    # ---- F3D 三维大屏(H02-D / L03;M6 并入统一策略层,沿 nvr_ 前缀惯例) ----
    Param("f3d_site_name", "园区站点名", SECTION_F3D_DATA, TYPE_STR,
          "云枢智造产业园", help="大屏顶栏站点名(L03 §2;大屏渲染前 html.escape)"),
    Param("f3d_connection_mode", "连接模式", SECTION_F3D_DATA, TYPE_CHOICE,
          "simulator", choices=("simulator", "mqtt", "external"),
          help="内置模拟器/MQTT 桥接(GAP-17)/仅外部接入(L03 §3.1)"),
    Param("f3d_push_interval_seconds", "推送周期", SECTION_F3D_DATA,
          TYPE_FLOAT, 2.0, ge=0.5, le=30, unit="秒",
          help="WS 全量帧周期(L03 §1:22 台 <5KB/帧)"),
    Param("f3d_mqtt_broker", "MQTT Broker", SECTION_F3D_DATA, TYPE_STR, "",
          help="tcp://host:1883;需 paho-mqtt,目标环境挂接(GAP-17)"),
    Param("f3d_alarm_delay_minutes", "告警延时", SECTION_F3D_ALARM,
          TYPE_FLOAT, 1.0, ge=0, le=180, unit="分钟",
          help="离线满该时长才转正式告警;等待期恢复视为抖动(L03 §6)"),
    Param("f3d_alarm_history_cap", "告警历史容量", SECTION_F3D_ALARM,
          TYPE_INT, 200, ge=10, le=5000,
          help="历史环形容量 HISTORY_CAP(L03 §6)"),
    Param("f3d_min_icon_px", "图钉最小像素", SECTION_F3D_RENDER, TYPE_INT,
          24, ge=0, le=96, unit="px",
          help="大屏默认显示设置;0=不钳制,本机可覆盖(L03 §2/§3.3)"),
    Param("f3d_ladder_enabled", "自适应降级阶梯", SECTION_F3D_RENDER,
          TYPE_BOOL, True,
          help="13-R-F3D-1:fps 驱动分档(关阴影→降贴图→降推送),恢复自动回升"),
    Param("f3d_fps_low_threshold", "降档 fps 阈值", SECTION_F3D_RENDER,
          TYPE_FLOAT, 24.0, ge=1, le=120,
          help="连续低于该值降一档(滞回下沿,13-R-F3D-1)"),
    Param("f3d_fps_high_threshold", "回升 fps 阈值", SECTION_F3D_RENDER,
          TYPE_FLOAT, 45.0, ge=1, le=240,
          help="连续高于该值回升一档(滞回上沿,13-R-F3D-1)"),
    Param("f3d_ladder_window", "滞回采样窗口", SECTION_F3D_RENDER, TYPE_INT,
          3, ge=1, le=60, help="连续 N 个采样越线才切档(防抖)"),
    Param("f3d_delta_sync_enabled", "布局差量同步", SECTION_F3D_RENDER,
          TYPE_BOOL, False,
          help="13-R-F3D-3(P2,默认关):关闭时保持全量广播语义(H09 K.3)"),
    Param("f3d_assistant_enabled", "AI 配置助手", SECTION_F3D_ASSIST,
          TYPE_BOOL, True,
          help="13-R-F3D-2:受限动作集事务化执行(dry-run→确认→原子回滚)"),
    Param("f3d_tx_log_cap", "事务日志留存条数", SECTION_F3D_ASSIST, TYPE_INT,
          500, ge=50, le=10000, help="助手事务日志有界留存(H08/H12)"),
)

SCHEMA_BY_KEY: dict = {param.key: param for param in SETTINGS_SCHEMA}
