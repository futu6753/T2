# -*- coding: utf-8 -*-
"""
@file    checker.py
@brief   检测判定树(L04 §3,契约):
         ISAPI deviceInfo 200 → online;在线且有离线录像通道(双开关)→
         abnormal;401/403 → auth_failed(异常);其他码 → abnormal;
         ISAPI 超时但 TCP 通 → timeout(异常);TCP 不通 ping 通 → abnormal;
         TCP+ICMP 均不通 → offline。探针可注入(离线环境无真设备,生产探针
         走标准库 socket/http.client;测试注入 fake)。凭据不出现在任何输出。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import socket
import subprocess
import time

STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_ABNORMAL = "abnormal"
STATUS_TIMEOUT = "timeout"           # 异常类
STATUS_AUTH_FAILED = "auth_failed"   # 异常类
STATUS_UNCHECKED = "unchecked"
ABNORMAL_FAMILY = (STATUS_ABNORMAL, STATUS_TIMEOUT, STATUS_AUTH_FAILED)


class IsapiTimeout(Exception):
    """ISAPI 请求超时(判定树分支信号)。"""


def default_tcp_probe(host: str, port: int, timeout: float) -> bool:
    """@brief TCP 连通性探测(标准库)"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def default_ping_probe(host: str, timeout: float) -> bool:
    """@brief ICMP 兜底(系统 ping;容器受限时配置关闭 icmp_enabled)"""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(max(int(timeout), 1)), host],
            capture_output=True, timeout=timeout + 2)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DeviceChecker:
    """单设备判定(探针注入点:isapi_probe/tcp_probe/ping_probe)。

    isapi_probe(host, port, username, password, timeout) →
        (http_status:int, offline_channels:list) 或 raise IsapiTimeout。
    生产实现按 Digest 优先、401 回退一次 Basic(L04 §3)。
    """

    def __init__(self, isapi_probe, tcp_probe=None, ping_probe=None,
                 icmp_enabled: bool = True, channel_check: bool = True,
                 channel_offline_abnormal: bool = False,
                 timeout_seconds: float = 8.0):
        """@brief 注入探针与开关(channel_offline_abnormal 默认关,T13)"""
        self._isapi = isapi_probe
        self._tcp = tcp_probe or default_tcp_probe
        self._ping = ping_probe or default_ping_probe
        self._icmp_enabled = icmp_enabled
        self._channel_check = channel_check
        self._channel_offline_abnormal = channel_offline_abnormal
        self._timeout = timeout_seconds

    def check(self, host: str, port: int, username: str,
              password: str) -> dict:
        """
        @brief  执行判定树 @return {status, detail, latency_ms,
                offline_channels}(detail 不含凭据)
        """
        started = time.monotonic()
        try:
            http_status, offline_channels = self._isapi(
                host, port, username, password, self._timeout)
        except IsapiTimeout:
            return self._after_isapi_timeout(host, port, started)
        except OSError:
            return self._after_tcp_layers(host, port, started)
        latency = int((time.monotonic() - started) * 1000)
        if http_status == 200:
            if (self._channel_check and self._channel_offline_abnormal
                    and offline_channels):
                names = ", ".join(str(no) for no in offline_channels[:5])
                return {"status": STATUS_ABNORMAL,
                        "detail": f"在线但有离线录像通道: {names}",
                        "latency_ms": latency,
                        "offline_channels": offline_channels}
            return {"status": STATUS_ONLINE, "detail": "ISAPI deviceInfo 200",
                    "latency_ms": latency,
                    "offline_channels": offline_channels}
        if http_status in (401, 403):
            return {"status": STATUS_AUTH_FAILED,
                    "detail": f"ISAPI 认证失败(HTTP {http_status})",
                    "latency_ms": latency, "offline_channels": []}
        return {"status": STATUS_ABNORMAL,
                "detail": f"ISAPI 非预期状态码 {http_status}",
                "latency_ms": latency, "offline_channels": []}

    def _after_isapi_timeout(self, host, port, started) -> dict:
        """@brief ISAPI 超时分支:TCP 通=timeout;否则降级 TCP/ICMP 判定"""
        if self._tcp(host, port, self._timeout):
            return {"status": STATUS_TIMEOUT,
                    "detail": "ISAPI 超时但 TCP 可达",
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "offline_channels": []}
        return self._after_tcp_layers(host, port, started)

    def _after_tcp_layers(self, host, port, started) -> dict:
        """@brief TCP 不通分支:ping 通=abnormal;全不通=offline"""
        latency = int((time.monotonic() - started) * 1000)
        if self._icmp_enabled and self._ping(host, self._timeout):
            return {"status": STATUS_ABNORMAL,
                    "detail": "TCP 不可达但 ICMP 可达(疑似服务故障)",
                    "latency_ms": latency, "offline_channels": []}
        return {"status": STATUS_OFFLINE,
                "detail": "TCP 与 ICMP 均不可达",
                "latency_ms": latency, "offline_channels": []}
