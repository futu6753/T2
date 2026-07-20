# -*- coding: utf-8 -*-
"""
@file    tracing.py
@brief   M10 追踪(L01 §7):X-Request-Id 经 ContextVar 贯通(响应体/
         响应头/日志同 id);stdlib JSON 结构化日志。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import contextvars
import json
import logging
import uuid
from datetime import datetime, timezone

_request_id = contextvars.ContextVar("adapter_request_id", default="")


def new_request_id() -> str:
    """@brief 生成请求 id"""
    return uuid.uuid4().hex


def set_request_id(value: str) -> str:
    """@brief 写入当前上下文(入站头有值则沿用)"""
    rid = value or new_request_id()
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    """@brief 读取当前上下文请求 id"""
    return _request_id.get()


class JsonLogFormatter(logging.Formatter):
    """JSON 行日志(自动携带 request_id)。"""

    def format(self, record: logging.LogRecord) -> str:
        """@brief 结构化输出"""
        doc = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def build_logger(name: str = "adapter") -> logging.Logger:
    """@brief 装配 JSON 日志器(幂等,不重复挂 handler)"""
    logger = logging.getLogger(name)
    if not any(isinstance(handler.formatter, JsonLogFormatter)
               for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
