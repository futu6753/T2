# -*- coding: utf-8 -*-
"""
@file    stream.py
@brief   事件流与实时通道:f3d_events 统一事件表(状态跃迁/告警转正/档位变更/
         布局变更)、WS 帧构建(snapshot→update 全量语义,13-R-F3D-3 差量同步
         为 P2 flag 默认关)、连接循环(fps 回报驱动 R-F3D-1,末档降推送频率)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import asyncio
import datetime
import json
import time

EVENT_TABLE_CAP = 1000        # 事件表留存上限(裁剪,时间线不清语义仅告警历史)
RECENT_EVENTS_LIMIT = 50      # /api/events 最近 50 条(L03 §7)


def _iso(epoch: float) -> str:
    """@brief epoch 秒 → ISO 串"""
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).isoformat()


def record_event(db, kind: str, device_id: str = "", building: str = "",
                 from_status: str = "", to_status: str = "", detail: str = "",
                 ts: float = None):
    """@brief 记一条事件并裁剪表容量"""
    ts = time.time() if ts is None else ts
    db.execute(
        "INSERT INTO f3d_events(ts, kind, device_id, building, from_status,"
        " to_status, detail) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (_iso(ts), kind, device_id, building, from_status, to_status, detail))
    db.execute(
        "DELETE FROM f3d_events WHERE id NOT IN"
        " (SELECT id FROM f3d_events ORDER BY id DESC LIMIT ?)",
        (EVENT_TABLE_CAP,))


def recent_events(db, limit: int = RECENT_EVENTS_LIMIT, kinds: tuple = None,
                  minutes: int = None) -> list:
    """@brief 最近事件(倒序;可按类型/时间窗过滤,L03 §7 /api/data/events)"""
    clauses, args = [], []
    if kinds:
        clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
        args.extend(kinds)
    if minutes:
        since = _iso(time.time() - minutes * 60)
        clauses.append("ts >= ?")
        args.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT ts, kind, device_id, building, from_status, to_status, detail"
        f" FROM f3d_events{where} ORDER BY id DESC LIMIT ?",
        tuple(args) + (limit,))
    return [{"ts": row[0], "kind": row[1], "device": row[2], "building": row[3],
             "from": row[4], "to": row[5], "detail": row[6]} for row in rows]


def build_frame(ctx, frame_type: str) -> dict:
    """@brief 构建 WS 帧(snapshot 与 update 同构全量,L03 §7 单向语义)"""
    return {
        "type": frame_type,
        "ver": ctx_ver(ctx),
        "data_rev": ctx.data_rev,
        "site": ctx.settings.get("f3d_site_name"),
        "tier": ctx.ladder.tier_name(),
        "min_icon_px": ctx.settings.get("f3d_min_icon_px"),
        "kpi": ctx.kpi(),
        "alarms": {"counts": ctx.alarms.counts(),
                   "active": ctx.alarms.active_list()},
        "devices": ctx.simulator.snapshot(),
        "events": recent_events(ctx.db, limit=20),
    }


def ctx_ver(ctx) -> str:
    """@brief 版本号(页面标题/healthz 一致性,L03 §8)"""
    from apps.factory3d.context import F3D_VER
    return F3D_VER


async def ws_session(websocket, ctx):
    """
    @brief  单连接推送循环:先 snapshot,再按周期全量 update;循环间隙接收
            {"type":"fps","value":x} 回报喂降级阶梯(R-F3D-1 端到端闭环);
            档位末档时逐连接降推送频率(push_interval 放大)。
    """
    await websocket.accept()
    await websocket.send_text(json.dumps(build_frame(ctx, "snapshot"),
                                         ensure_ascii=False))
    try:
        while True:
            base = float(ctx.settings.get("f3d_push_interval_seconds"))
            interval = ctx.ladder.push_interval(base)
            deadline = time.monotonic() + interval
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(),
                                                 timeout=remain)
                except asyncio.TimeoutError:
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if message.get("type") == "fps":
                    ctx.ladder.feed(message.get("value", 0))
            ctx.tick()
            await websocket.send_text(json.dumps(build_frame(ctx, "update"),
                                                 ensure_ascii=False))
    except Exception:      # 断线/关闭:客户端指数退避重连(L03 §7)
        return
