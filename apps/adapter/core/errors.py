# -*- coding: utf-8 -*-
"""
@file    errors.py
@brief   适配器统一错误语义(L01 §5 / H02-F1 R9):业务层只抛 BusinessException
         子类,api 层全局处理器统一转 AdapterResult{code,message,request_id,data}。
         条件必填由 require_fields 校验(→400,而非框架 422;H07 L1-08)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""


class BusinessException(Exception):
    """业务异常基类:携带 R9 HTTP 状态码与机读 code。"""

    http_status = 500
    code = "internal_error"

    def __init__(self, message: str, data: dict = None):
        """@brief 记录人话消息与可选附加数据"""
        super().__init__(message)
        self.message = message
        self.data = data or {}


class FieldError(BusinessException):
    """400:字段缺失/取值非法(含 NaN/Inf/范围,L1-08)。"""

    http_status = 400
    code = "invalid_field"


class SignatureError(BusinessException):
    """401:入站 Webhook 验签失败(strict 模式)。"""

    http_status = 401
    code = "signature_invalid"


class UpstreamRejected(BusinessException):
    """409:上游明确拒绝,或幂等键冲突。"""

    http_status = 409
    code = "upstream_rejected"


class IdempotencyConflict(UpstreamRejected):
    """409:同幂等键携带不同请求体。"""

    code = "idempotency_conflict"


class FeatureDisabledError(BusinessException):
    """501:feature 未启用/规划中被调用。"""

    http_status = 501
    code = "feature_disabled"


class UpstreamError(BusinessException):
    """502:上游异常(连接失败/响应不可解析/5xx)。"""

    http_status = 502
    code = "upstream_error"


class ReplyTimeout(BusinessException):
    """504:命令 ack 等待超时。"""

    http_status = 504
    code = "reply_timeout"


class ConfigError(BusinessException):
    """500:配置值不可用于出网(如请求头含非 latin-1 字符,H06-E17/M17)。"""

    code = "config_error"


def require_fields(payload: dict, names, context: str = ""):
    """
    @brief  条件必填校验:任一字段缺失或为空 → FieldError(400)
    @param  payload 请求体字典
    @param  names   必填字段名序列
    @param  context 报错上下文(如 "command=takeoff")
    """
    missing = [name for name in names
               if payload.get(name) in (None, "", [], {})]
    if missing:
        suffix = f"({context})" if context else ""
        raise FieldError(f"缺少必填字段{suffix}:{','.join(missing)}",
                         data={"missing": missing})


def require_choice(payload: dict, name: str, choices):
    """@brief 枚举取值校验:字段存在但不在允许集合 → FieldError(400)"""
    value = payload.get(name)
    if value not in choices:
        raise FieldError(
            f"字段 {name} 取值非法:{value!r},允许 {sorted(map(str, choices))}",
            data={"field": name, "value": value})
    return value
