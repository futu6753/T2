# -*- coding: utf-8 -*-
"""
@file    config.py
@brief   适配器配置(L01 §8):.env 键与 Settings 字段一一对应(大写)。
         M17 env 硬化:注释必须独立成行——本加载器不把行内 "#" 当注释
         (dotenv 解析差异曾把注释文本当 HTTP 请求头值发出,引发 latin-1
         编码崩溃),但会把疑似行内注释记入 warnings,由 /status/runtime
         与控制台 env-warn 黄条如实上报(H06-E17:不得静默)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from dataclasses import dataclass, field, fields


@dataclass
class Settings:
    """适配器运行配置:字段名 = .env 键(小写化);默认值为 L01 §8 冻结值。"""

    # 通用
    command_reply_timeout_s: float = 30.0
    command_poll_interval_s: float = 2.0
    idempotency_ttl_s: float = 600.0
    sink_queue_maxlen: int = 1000
    feature_file: str = "feature_list.json"
    recent_events_maxlen: int = 500
    raw_log_maxlen: int = 200
    # 轮询
    ingest_zg_robots_interval_s: float = 30.0
    ingest_zg_alarms_interval_s: float = 60.0
    ingest_zg_tasks_interval_s: float = 60.0
    ingest_sky_batches_interval_s: float = 5.0
    ingest_siyun_props_interval_s: float = 15.0
    ingest_siyun_tasks_interval_s: float = 60.0
    siyun_tasks_lookback_s: int = 3600
    siyun_tasks_page_size: int = 50
    dedupe_ttl_s: float = 3600.0
    poller_tick_s: float = 0.5
    # 外发
    downstream_url: str = ""
    downstream_secret: str = ""
    forward_batch_max: int = 50
    forward_flush_interval_s: float = 2.0
    forward_max_retries: int = 5
    forward_backoff_base_s: float = 1.0
    dead_letter_maxlen: int = 500
    # 织光
    zg_base_url: str = ""
    zg_app_key: str = ""
    zg_app_secret: str = ""
    zg_sign_mode: str = "hmac_v1"          # hmac_v1|off,TODO(GAP-21) 文档化假设
    zg_verify_webhook: str = "strict"      # strict|log|off
    # 星逻
    skysys_auth_base: str = "https://open-api.skysys.cn"
    skysys_gw_b_base: str = ""
    skysys_access_key: str = ""
    skysys_access_secret: str = ""
    skysys_product: str = "yg"
    skysys_token_header: str = "accessToken"   # TODO(GAP-23) 头名候选
    skysys_token_ttl_s: float = 1800.0
    # 司运
    siyun_base_url: str = ""
    siyun_group_id: str = ""
    siyun_ak: str = ""
    siyun_sk: str = ""
    siyun_ak_header: str = ""              # TODO(GAP-22) 鉴权头三兜底
    siyun_auth_header: str = "Authorization"
    siyun_auth_value: str = ""
    # 司空2
    fh2_base_url: str = ""
    fh2_user_token: str = ""
    # 模拟器
    simulator_sns: str = ""
    # 诊断(非 .env 键)
    warnings: list = field(default_factory=list)

    def simulator_sn_list(self) -> list:
        """@brief 模拟器 SN 列表(逗号分隔)"""
        return [sn.strip() for sn in self.simulator_sns.split(",") if sn.strip()]


def parse_env_text(text: str) -> tuple:
    """
    @brief  解析 .env 文本(M17 硬化):整行 # 为注释;行内 # 保留进值并告警
    @return (键值字典, 告警列表)
    """
    values, warnings = {}, []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            warnings.append(f".env 第 {lineno} 行无 '=',已忽略")
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            warnings.append(
                f".env 第 {lineno} 行 {key} 疑似行内注释(注释必须独立成行,"
                f"M17):值按原样保留 {value!r}")
        values[key] = value
    return values, warnings


def load_settings(env: dict, extra_warnings: list = None) -> Settings:
    """@brief 由环境字典装配 Settings(大写键→小写字段,类型按默认值转换)"""
    settings = Settings()
    settings.warnings = list(extra_warnings or [])
    for spec in fields(Settings):
        if spec.name == "warnings":
            continue
        raw = env.get(spec.name.upper())
        if raw is None:
            continue
        default = getattr(settings, spec.name)
        try:
            if isinstance(default, bool):
                value = raw.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                value = int(raw)
            elif isinstance(default, float):
                value = float(raw)
            else:
                value = raw
        except ValueError:
            settings.warnings.append(
                f"{spec.name.upper()} 值 {raw!r} 无法解析,沿用默认 {default!r}")
            continue
        setattr(settings, spec.name, value)
    return settings


def ensure_header_safe(name: str, value: str):
    """
    @brief  出网请求头安全检查(M17 防回潮):非 latin-1 直接给人话 ConfigError,
            而不是在 http 库深处 UnicodeEncodeError 崩溃
    """
    from apps.adapter.core.errors import ConfigError
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ConfigError(
            f"请求头 {name} 含非 latin-1 字符(常见根因:.env 行内注释被并入值,"
            f"注释必须独立成行):{value!r}") from exc
    return value
