# -*- coding: utf-8 -*-
"""
@file    assistant.py
@brief   13-R-F3D-2 AI 配置助手事务化引擎:模型末尾 ```json {"actions":[…]}```
         → 注册表逐条校验 → dry-run 预览(逐条变更差异,零落库)→ 用户确认 →
         原子执行(任一失败整体回滚零残留,布局单次 data_rev+1)。
         危险操作 confirm 语义不变;编辑域受会话锁保护(session_locked);
         事务日志有界留存(f3d_tx_log);B7 恶意/越权评测误执行 MUST = 0。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import copy
import datetime
import json
import re
import secrets

from gd_storage import events

from apps.factory3d.assistant_actions import (
    DANGER_ACTIONS,
    ActionError,
    AssistantActionsMixin,
)

SCOPE_DATA = "data"       # 数据管理台:改动持久化
SCOPE_EDIT = "edit"       # 交互式编辑台:改动实时生效(受会话锁)
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class TxState:
    """一笔事务的暂存态:布局深拷贝 + 设置暂存 + 运行时待办 + 差异清单。"""

    def __init__(self, doc: dict):
        """@brief 以当前布局深拷贝开始事务"""
        self.doc = copy.deepcopy(doc)
        self.structural = False
        self.settings = {}
        self.toggles = []
        self.acks = []
        self.diffs = []


class AssistantEngine(AssistantActionsMixin):
    """助手事务编排:extract → validate+apply(事务态) → dry-run/commit。"""

    def __init__(self, ctx):
        """@brief 绑定 F3D 上下文;预览事务暂存表(tx_id → actions)"""
        self._ctx = ctx
        self._settings = ctx.settings
        self._registry = self._build_registry()
        self._pending = {}

    # ---- 提取与校验 -------------------------------------------------------
    def extract_actions(self, text: str):
        """@brief 抽取模型输出末尾 JSON 动作块 @return actions 列表|None"""
        matches = _JSON_BLOCK.findall(text or "")
        if not matches:
            return None
        try:
            payload = json.loads(matches[-1])
        except ValueError:
            return None
        actions = payload.get("actions")
        return actions if isinstance(actions, list) else None

    def _check_shape(self, actions: list, scope: str):
        """@brief 逐条形状/危险/会话锁前置校验(拒绝即零副作用,B7)"""
        if scope not in (SCOPE_DATA, SCOPE_EDIT):
            raise ActionError(f"未知 scope: {scope}")
        if not self._settings.get("f3d_assistant_enabled"):
            raise ActionError("AI 配置助手已停用")
        if scope == SCOPE_EDIT and not self._ctx.edit_session_active:
            raise ActionError("session_locked:交互式编辑会话未启动,动作已被拦截")
        if not actions:
            raise ActionError("动作列表为空")
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or \
                    not isinstance(action.get("action"), str):
                raise ActionError(f"第 {index + 1} 条动作格式非法")
            name = action["action"]
            if name not in self._registry:
                raise ActionError(f"第 {index + 1} 条动作未注册: {name}")
            args = action.get("args", {})
            if not isinstance(args, dict):
                raise ActionError(f"第 {index + 1} 条动作 args 须为对象")
            if name in DANGER_ACTIONS and args.get("confirm") is not True:
                raise ActionError(
                    f"第 {index + 1} 条「{name}」为危险操作,"
                    f"需 args.confirm=true,请先向用户确认")

    def _apply_all(self, actions: list) -> TxState:
        """@brief 在事务态上逐条执行(任何一条抛错=整笔失败,事务态丢弃)"""
        doc, _ = self._ctx.layouts.get()
        tx = TxState(doc)
        for index, action in enumerate(actions):
            handler = self._registry[action["action"]]
            try:
                handler(tx, action.get("args", {}))
            except (ActionError, ValueError) as exc:
                raise ActionError(
                    f"第 {index + 1} 条「{action['action']}」失败: {exc}"
                ) from exc
        return tx

    # ---- 事务日志与审计 ---------------------------------------------------
    def _log(self, scope: str, operator: str, phase: str, actions: list,
             result: dict):
        """@brief 事务日志有界留存 + 统一审计事件"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._ctx.db.execute(
            "INSERT INTO f3d_tx_log(ts, scope, operator, phase, actions_json,"
            " result_json) VALUES(?, ?, ?, ?, ?, ?)",
            (now, scope, operator, phase,
             json.dumps(actions, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False)))
        cap = int(self._settings.get("f3d_tx_log_cap"))
        self._ctx.db.execute(
            "DELETE FROM f3d_tx_log WHERE id NOT IN"
            " (SELECT id FROM f3d_tx_log ORDER BY id DESC LIMIT ?)", (cap,))
        self._ctx.audit.append(
            operator, events.AI_ACTION_EXECUTED,
            {"scope": scope, "phase": phase, "count": len(actions)}, "0.0.0.0")

    def tx_log(self, limit: int = 50) -> list:
        """@brief 事务日志查询(倒序)"""
        return [{"ts": row[0], "scope": row[1], "operator": row[2],
                 "phase": row[3]}
                for row in self._ctx.db.query(
                    "SELECT ts, scope, operator, phase FROM f3d_tx_log"
                    " ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- 对外三步:preview → confirm(tx_id) → execute ---------------------
    def preview(self, text: str, scope: str, operator: str) -> dict:
        """
        @brief  dry-run 预览:逐条展示将发生的变更差异;不落任何库
        @return {ok, diffs, tx_id} 或 {ok: False, error}
        """
        actions = self.extract_actions(text)
        if actions is None:
            return {"ok": True, "actions": 0, "diffs": [],
                    "note": "回复中未检出动作 JSON,按纯文本处理"}
        try:
            self._check_shape(actions, scope)
            tx = self._apply_all(actions)
        except ActionError as exc:
            self._log(scope, operator, "rejected", actions,
                      {"error": str(exc)})
            return {"ok": False, "error": str(exc)}
        tx_id = secrets.token_hex(8)
        self._pending[tx_id] = {"actions": actions, "scope": scope}
        self._log(scope, operator, "dry_run", actions, {"diffs": tx.diffs})
        return {"ok": True, "actions": len(actions), "diffs": tx.diffs,
                "tx_id": tx_id}

    def execute_pending(self, tx_id: str, operator: str) -> dict:
        """@brief 用户确认后按 tx_id 原子执行预览过的事务"""
        pending = self._pending.pop(tx_id, None)
        if pending is None:
            return {"ok": False, "error": "预览事务不存在或已执行"}
        return self.execute(pending["actions"], pending["scope"], operator)

    def execute(self, actions: list, scope: str, operator: str) -> dict:
        """
        @brief  原子执行:事务态全部成功 → 布局单次落库(structural 才 +1)+
                设置逐项写覆盖层 + 运行时待办;任一失败整体不落(回滚零残留)
        """
        try:
            self._check_shape(actions, scope)
            tx = self._apply_all(actions)
        except ActionError as exc:
            self._log(scope, operator, "rejected", actions,
                      {"error": str(exc)})
            return {"ok": False, "error": str(exc), "rolled_back": True}
        doc, data_rev = self._ctx.layouts.replace(tx.doc,
                                                  structural=tx.structural)
        self._ctx.layout_changed(doc, data_rev)
        for key, value in tx.settings.items():
            self._settings.set_override(key, value, operator, "0.0.0.0",
                                        audit_writer=self._ctx.audit)
        for device_id in tx.toggles:
            self._ctx.toggle_device(device_id)
        for ack in tx.acks:
            self._ctx.alarms.ack(**ack)
        self._log(scope, operator, "executed", actions, {"diffs": tx.diffs})
        return {"ok": True, "applied": True, "diffs": tx.diffs,
                "data_rev": data_rev}
