# -*- coding: utf-8 -*-
"""
@file    dispatch.py
@brief   命令下行调度(L01 §4/§5):条件必填校验(→400,而非 422)、
         幂等(TTL 600s;同键同体重放缓存结果、同键异体 409)、
         30s reply 预算(ack 超时→504;ack 到、终态未确认→accepted,
         预算内每 poll_interval 礼貌轮询终态,确认则 succeeded;
         上游明确拒绝→409)。reply 语义参数(ack 超时/轮询间隔/终态判定
         谓词/句柄候选路径)来自厂商映射声明(13-R-AD-1,原 R-AD-2 并入)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import json
import time

from apps.adapter.core.dsl import get_path
from apps.adapter.core.errors import (FieldError, IdempotencyConflict,
                                      ReplyTimeout, UpstreamRejected,
                                      require_choice, require_fields)
from apps.adapter.core.vendors.transport import TransportTimeout

STATUS_SUCCEEDED = "succeeded"
STATUS_ACCEPTED = "accepted"

XINGLUO_COMMANDS = ("takeoff", "pause", "resume", "return_home")
FLYCART_COMMANDS = ("create_task", "start_task", "edit_task_status", "raw_cmd")
ROBOT_COMMANDS = ("forced_inbound", "forced_outbound", "temporary_cleaning")


def validate_xingluo(payload: dict) -> str:
    """@brief 星逻条件必填(L01 §4):takeoff→site_id+mission_id;其余→uav_id"""
    command = require_choice(payload, "command", XINGLUO_COMMANDS)
    if command == "takeoff":
        require_fields(payload, ("site_id", "mission_id"), "command=takeoff")
    else:
        require_fields(payload, ("uav_id",), f"command={command}")
    return command


def validate_flycart(payload: dict) -> str:
    """@brief FlyCart 条件必填:device_sn*;task/task_id/status/cmd 按命令"""
    require_fields(payload, ("device_sn",))
    command = require_choice(payload, "command", FLYCART_COMMANDS)
    if command == "create_task":
        require_fields(payload, ("task",), "command=create_task")
        if not isinstance(payload.get("task"), dict):
            raise FieldError("字段 task 必须为对象(dict)")
    if command in ("start_task", "edit_task_status"):
        require_fields(payload, ("task_id",), f"command={command}")
    if command == "edit_task_status":
        require_fields(payload, ("status",), "command=edit_task_status")
    if command == "raw_cmd":
        require_fields(payload, ("cmd",), "command=raw_cmd")
        if not isinstance(payload.get("cmd"), dict):
            raise FieldError("字段 cmd 必须为对象(dict)")
    return command


def validate_cleaning_robot(payload: dict) -> str:
    """@brief 织光清洁机器人条件必填:robot_id*;status/排期字段按命令"""
    require_fields(payload, ("robot_id",))
    command = require_choice(payload, "command", ROBOT_COMMANDS)
    if command in ("forced_inbound", "forced_outbound"):
        require_choice(payload, "status", ("open", "close"))
    if command == "temporary_cleaning":
        method = require_choice(payload, "scheduling_method",
                                ("asap", "specifiedTime"))
        if method == "specifiedTime":
            require_fields(payload, ("scheduled_cleaning_at",),
                           "scheduling_method=specifiedTime")
            stamp = str(payload["scheduled_cleaning_at"])
            try:
                time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise FieldError(
                    f"scheduled_cleaning_at 格式非法(应为 YYYY-MM-DD "
                    f"HH:mm:ss):{stamp!r}")
    return command


def _fingerprint(payload: dict) -> str:
    """@brief 幂等指纹(剔除幂等键本身后的规范化 JSON 摘要)"""
    body = {key: value for key, value in payload.items()
            if key != "idempotency_key" and value is not None}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CommandDispatcher:
    """命令调度器(时钟/睡眠可注入,测试用假时钟零等待)。"""

    def __init__(self, settings, clock=time.monotonic, sleeper=time.sleep):
        """@brief 绑定全局 reply 默认与幂等缓存"""
        self.settings = settings
        self._clock = clock
        self._sleep = sleeper
        self._idempotency = {}
        self.stats = {"sent": 0, "succeeded": 0, "accepted": 0,
                      "rejected": 0, "timeout": 0, "replayed": 0}

    def _idempotency_lookup(self, key: str, fingerprint: str):
        """@brief 幂等查询:同键同体→缓存结果;同键异体→409;过期即删"""
        if not key:
            return None
        now = self._clock()
        entry = self._idempotency.get(key)
        if entry and now - entry["ts"] > self.settings.idempotency_ttl_s:
            self._idempotency.pop(key, None)
            entry = None
        if not entry:
            return None
        if entry["fingerprint"] != fingerprint:
            raise IdempotencyConflict(
                f"幂等键 {key} 已绑定不同请求体(TTL "
                f"{int(self.settings.idempotency_ttl_s)}s 内)")
        self.stats["replayed"] += 1
        return entry["result"]

    def _reply_params(self, spec) -> dict:
        """@brief 厂商声明 reply 参数(缺省回退全局默认)"""
        defaults = {
            "ack_timeout_s": self.settings.command_reply_timeout_s,
            "poll_interval_s": self.settings.command_poll_interval_s,
        }
        return spec.reply_params(defaults) if spec else dict(
            defaults, terminal=None)

    @staticmethod
    def _extract_handle(reply: dict, ack: dict):
        """@brief 从 ack 提取轮询句柄(候选路径逗号声明,首个非空)"""
        handle_rule = reply.get("handle") or {}
        for path in str(handle_rule.get("candidates", "")).split(","):
            path = path.strip()
            if not path:
                continue
            value = get_path(ack, path)
            if value not in (None, ""):
                return str(value)
        return None

    def _poll_terminal(self, client, reply: dict, handle: str,
                       deadline: float) -> str:
        """@brief 30s 预算内礼貌轮询终态(succeeded/失败 409/预算尽 accepted)"""
        terminal = reply.get("terminal") or {}
        query_name = terminal.get("query")
        if not query_name or not handle:
            return STATUS_ACCEPTED
        succeeded = [str(item) for item in (terminal.get("succeeded") or [])]
        failed = [str(item) for item in (terminal.get("failed") or [])]
        interval = float(reply.get("poll_interval_s", 2.0))
        while self._clock() < deadline:
            doc = client.query(query_name, handle)
            value = str(get_path(doc, terminal.get("path", "status")))
            if value in succeeded:
                return STATUS_SUCCEEDED
            if value in failed:
                raise UpstreamRejected(
                    f"上游报告命令终态失败:{value}",
                    data={"terminal_status": value, "handle": handle})
            self._sleep(min(interval, max(0.0, deadline - self._clock())))
        return STATUS_ACCEPTED

    def dispatch(self, client, spec, payload: dict) -> dict:
        """
        @brief  下发一条命令(校验已由 validate_* 完成)
        @return {status, ack, handle}(status ∈ succeeded|accepted)
        """
        key = payload.get("idempotency_key") or ""
        fingerprint = _fingerprint(payload)
        cached = self._idempotency_lookup(key, fingerprint)
        if cached is not None:
            return cached
        reply = self._reply_params(spec)
        self.stats["sent"] += 1
        start = self._clock()
        try:
            ack = client.send_command(payload)
        except TransportTimeout as exc:
            self.stats["timeout"] += 1
            raise ReplyTimeout(
                f"命令 ack 等待超时({reply['ack_timeout_s']:.0f}s 预算)"
            ) from exc
        except UpstreamRejected:
            self.stats["rejected"] += 1
            raise
        handle = self._extract_handle(reply, ack)
        deadline = start + float(reply["ack_timeout_s"])
        try:
            status = self._poll_terminal(client, reply, handle, deadline)
        except UpstreamRejected:
            self.stats["rejected"] += 1
            raise
        self.stats[status] += 1
        result = {"status": status, "ack": ack, "handle": handle}
        if key:
            self._idempotency[key] = {"fingerprint": fingerprint,
                                      "result": result, "ts": self._clock()}
        return result
