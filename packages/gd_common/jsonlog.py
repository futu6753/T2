# -*- coding: utf-8 -*-
"""
@file    jsonlog.py
@brief   结构化 JSON 日志(H07 L3 日志条款 / H01 ARC-6):禁 print,全链路 X-Request-Id 贯通
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import contextvars
import datetime
import json
import logging
import sys

# 请求追踪 ID 上下文变量:中间件写入,日志与响应头统一读取(参考 adapter M10)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _JsonFormatter(logging.Formatter):
    """JSON 行格式化器:每行一个 JSON 对象,含时间、级别、模块、请求 ID。"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        # 额外结构化字段:调用方通过 extra={"ctx": {...}} 传入
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            entry.update(ctx)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """
    @brief  获取带 JSON 格式化的结构化日志器(幂等,重复调用不重复挂 handler)
    @param  name 日志器名称,建议用模块名
    @return 配置完成的 Logger 实例
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
