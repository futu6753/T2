# -*- coding: utf-8 -*-
"""
@file    assistant_actions.py
@brief   AI 配置助手动作注册表 Mixin(L03 §5 全集 23 项;文档口径"20 个"为
         遗留计数,以清单为准):全部动作在事务态 TxState 上执行——布局改动落
         深拷贝、设置改动入暂存、运行时操作(toggle/ack)入待办,提交阶段才
         触达真实状态(13-R-F3D-2 原子语义前提)。危险操作需 args.confirm=true。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_policy.schema import (
    SCHEMA_BY_KEY,
    TYPE_BOOL,
    TYPE_CHOICE,
    TYPE_FLOAT,
    TYPE_INT,
)
from gd_policy.service import SOURCE_ENV

from apps.factory3d import layout as lo

DANGER_ACTIONS = ("remove_zone", "remove_building", "remove_device",
                  "reset_layout")


class ActionError(ValueError):
    """动作校验/执行违规(逐条回显 bad 芯片;B7 拒绝路径)。"""


class AssistantActionsMixin:
    """动作实现层:被 AssistantEngine 继承(engine + mixin 拆分守 500 行红线)。"""

    # ---- 暂存设置(带 schema 校验与 env 锁定预检) -------------------------
    def _stage_setting(self, tx, key: str, value, label: str):
        """@brief 校验并暂存一项设置变更(提交阶段统一写覆盖层)"""
        param = SCHEMA_BY_KEY.get(key)
        if param is None:
            raise ActionError(f"未知配置键: {key}")
        if self._settings.get_with_source(key)[1] == SOURCE_ENV:
            raise ActionError(f"{label} 已由环境变量锁定,请修改部署配置")
        if param.type == TYPE_BOOL:
            if not isinstance(value, bool):
                raise ActionError(f"{label} 须为布尔值")
        elif param.type in (TYPE_INT, TYPE_FLOAT):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ActionError(f"{label} 须为数值")
            if param.ge is not None and value < param.ge:
                raise ActionError(f"{label}={value} 低于下限 {param.ge}")
            if param.le is not None and value > param.le:
                raise ActionError(f"{label}={value} 超过上限 {param.le}")
        elif param.type == TYPE_CHOICE:
            if value not in param.choices:
                raise ActionError(f"{label} 非法取值 {value!r},可选 {param.choices}")
        elif not isinstance(value, str):
            raise ActionError(f"{label} 须为字符串")
        old = self._settings.get(key)
        tx.settings[key] = value
        tx.diffs.append(f"{label}: {old!r} → {value!r}")

    def _find_runtime_device(self, tx, device_id):
        """@brief 校验设备存在于事务态布局(toggle/告警按设备消除的前提)"""
        if not isinstance(device_id, str) or not device_id:
            raise ActionError("device_id 须为非空字符串")
        lo.find_device(tx.doc, device_id)
        return device_id

    # ---- 注册表 -----------------------------------------------------------
    def _build_registry(self) -> dict:
        """@brief 动作名 → 处理函数 fn(tx, args)(全部经逐条校验后执行)"""
        return {
            "set_site_name": self._act_set_site_name,
            "set_connection": self._act_set_connection,
            "set_alarm": self._act_set_alarm,
            "set_display": self._act_set_display,
            "set_ai": self._act_set_ai,
            "add_zone": self._act_add_zone,
            "rename_zone": self._act_rename_zone,
            "remove_zone": self._act_remove_zone,
            "set_zone_focus": self._act_set_zone_focus,
            "set_zone_center": self._act_set_zone_center,
            "add_building": self._act_add_building,
            "rename_building": self._act_rename_building,
            "set_building_type": self._act_set_building_type,
            "set_building_model": self._act_set_building_model,
            "move_building": self._act_move_building,
            "remove_building": self._act_remove_building,
            "add_device": self._act_add_device,
            "patch_device": self._act_patch_device,
            "remove_device": self._act_remove_device,
            "toggle_device": self._act_toggle_device,
            "ack_alarm": self._act_ack_alarm,
            "set_home": self._act_set_home,
            "reset_layout": self._act_reset_layout,
        }

    # ---- 设置类动作 -------------------------------------------------------
    def _act_set_site_name(self, tx, args):
        """@brief 改站点名(大屏渲染前 html.escape,XSS 防线在页面层)"""
        name = args.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ActionError("站点名须为 1~80 字")
        self._stage_setting(tx, "f3d_site_name", name, "园区站点名")

    def _act_set_connection(self, tx, args):
        """@brief 连接模式/刷新周期/MQTT Broker"""
        if "mode" in args:
            self._stage_setting(tx, "f3d_connection_mode", args["mode"],
                                "连接模式")
        if "interval" in args:
            self._stage_setting(tx, "f3d_push_interval_seconds",
                                args["interval"], "推送周期")
        if "broker" in args:
            self._stage_setting(tx, "f3d_mqtt_broker", args["broker"],
                                "MQTT Broker")
        if not any(key in args for key in ("mode", "interval", "broker")):
            raise ActionError("set_connection 至少提供 mode/interval/broker 之一")

    def _act_set_alarm(self, tx, args):
        """@brief 告警延时分钟(0~180,允许小数)"""
        if "delay_min" not in args:
            raise ActionError("set_alarm 缺少 delay_min")
        self._stage_setting(tx, "f3d_alarm_delay_minutes", args["delay_min"],
                            "告警延时")

    def _act_set_display(self, tx, args):
        """@brief 图钉最小像素(0~96)"""
        if "min_icon_px" not in args:
            raise ActionError("set_display 缺少 min_icon_px")
        self._stage_setting(tx, "f3d_min_icon_px", args["min_icon_px"],
                            "图钉最小像素")

    def _act_set_ai(self, tx, args):
        """@brief AI 简报/助手开关;不可改密钥(L03 §5 set_ai 契约,B7 锚点)"""
        if "api_key" in args:
            raise ActionError("助手不可修改 API Key,请在数据管理台人工配置")
        if "enabled" not in args:
            raise ActionError("set_ai 缺少 enabled")
        self._stage_setting(tx, "f3d_assistant_enabled", args["enabled"],
                            "AI 配置助手")

    # ---- 结构类动作(全部落 tx.doc 深拷贝) -------------------------------
    def _act_add_zone(self, tx, args):
        """@brief 新增场区"""
        zone = lo.add_zone(tx.doc, args.get("name", ""))
        tx.structural = True
        tx.diffs.append(f"新增场区「{zone['name']}」(id={zone['id']})")

    def _act_rename_zone(self, tx, args):
        """@brief 场区改名"""
        zone = lo.find_zone(tx.doc, args.get("zone_id", ""))
        name = args.get("name", "")
        if not name or len(name) > 40:
            raise ActionError("场区名称须为 1~40 字")
        tx.diffs.append(f"场区改名「{zone['name']}」→「{name}」")
        zone["name"] = name

    def _act_remove_zone(self, tx, args):
        """@brief 删除场区(连带楼宇设备;危险,须 confirm)"""
        zone = lo.find_zone(tx.doc, args.get("zone_id", ""))
        lo.remove_zone(tx.doc, zone["id"])
        tx.structural = True
        tx.diffs.append(f"删除场区「{zone['name']}」及其全部楼宇设备")

    def _act_set_zone_focus(self, tx, args):
        """@brief 场区透视切入角(elev 10~80;theta 或 follow)"""
        theta = "keep"
        if args.get("follow"):
            theta = None
        elif "theta" in args:
            theta = args["theta"]
        lo.set_zone_focus(tx.doc, args.get("zone_id", ""),
                          elev=args.get("elev"), theta=theta)
        tx.diffs.append(f"场区 {args.get('zone_id')} 透视切入角已更新")

    def _act_set_zone_center(self, tx, args):
        """@brief 场区旋转中心偏移(dx,dz 或 reset)"""
        lo.set_zone_center(tx.doc, args.get("zone_id", ""),
                           dx=args.get("dx"), dz=args.get("dz"),
                           reset=bool(args.get("reset")))
        tx.diffs.append(f"场区 {args.get('zone_id')} 旋转中心已更新")

    def _act_add_building(self, tx, args):
        """@brief 新增楼宇"""
        building = lo.add_building(tx.doc, args.get("zone_id", ""),
                                   args.get("name", ""),
                                   btype=args.get("type", "warehouse"))
        tx.structural = True
        tx.diffs.append(f"新增楼宇「{building['name']}」(id={building['id']})")

    def _act_rename_building(self, tx, args):
        """@brief 楼宇改名"""
        lo.rename_building(tx.doc, args.get("building_id", ""),
                           args.get("name", ""))
        tx.diffs.append(f"楼宇 {args.get('building_id')} 改名为"
                        f"「{args.get('name')}」")

    def _act_set_building_type(self, tx, args):
        """@brief 改楼宇类型"""
        lo.set_building_type(tx.doc, args.get("building_id", ""),
                             args.get("type", ""))
        tx.diffs.append(f"楼宇 {args.get('building_id')} 类型改为"
                        f" {args.get('type')}")

    def _act_set_building_model(self, tx, args):
        """@brief 绑定外观模型(null=程序化)"""
        lo.set_building_model(tx.doc, args.get("building_id", ""),
                              args.get("model"))
        tx.diffs.append(f"楼宇 {args.get('building_id')} 外观模型改为"
                        f" {args.get('model')!r}")

    def _act_move_building(self, tx, args):
        """@brief 移动楼宇(夹取+重叠校验,违规即整笔失败)"""
        if "dx" not in args or "dz" not in args:
            raise ActionError("move_building 缺少 dx/dz")
        if any(isinstance(args[key], bool) or
               not isinstance(args[key], (int, float))
               for key in ("dx", "dz")):
            raise ActionError("dx/dz 须为数值")
        lo.move_building(tx.doc, args.get("building_id", ""),
                         args["dx"], args["dz"])
        tx.structural = True
        tx.diffs.append(f"楼宇 {args.get('building_id')} 移至"
                        f" ({args['dx']}, {args['dz']})")

    def _act_remove_building(self, tx, args):
        """@brief 删除楼宇(危险,须 confirm)"""
        zone, building = lo.find_building(tx.doc, args.get("building_id", ""))
        lo.remove_building(tx.doc, building["id"])
        tx.structural = True
        tx.diffs.append(f"删除楼宇「{building['name']}」及其设备")

    def _act_add_device(self, tx, args):
        """@brief 新增设备(building_id 缺省=室外)"""
        device = lo.add_device(
            tx.doc, args.get("name", ""), args.get("type", ""),
            building_id=args.get("building_id"), room=args.get("room", ""),
            ip=args.get("ip", ""), pos=args.get("pos"))
        tx.structural = True
        tx.diffs.append(f"新增设备「{device['name']}」(id={device['id']})")

    def _act_patch_device(self, tx, args):
        """@brief 改设备字段(白名单校验在 layout 层,B7 防越权写)"""
        fields = args.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ActionError("patch_device 缺少 fields 对象")
        lo.patch_device(tx.doc, args.get("device_id", ""), fields)
        tx.structural = True
        tx.diffs.append(f"设备 {args.get('device_id')} 更新字段"
                        f" {sorted(fields)}")

    def _act_remove_device(self, tx, args):
        """@brief 删除设备(危险,须 confirm)"""
        device_id = args.get("device_id", "")
        lo.remove_device(tx.doc, device_id)
        tx.structural = True
        tx.diffs.append(f"删除设备 {device_id}")

    # ---- 运行时动作(入待办,提交阶段触达) -------------------------------
    def _act_toggle_device(self, tx, args):
        """@brief 模拟掉线/恢复(提交时经统一状态入口)"""
        device_id = self._find_runtime_device(tx, args.get("device_id"))
        tx.toggles.append(device_id)
        tx.diffs.append(f"切换设备 {device_id} 在线状态")

    def _act_ack_alarm(self, tx, args):
        """@brief 消除告警(alarm_id|device_id|all)"""
        if args.get("all"):
            tx.acks.append({"ack_all": True})
            tx.diffs.append("消除全部活动告警")
            return
        if "alarm_id" in args:
            if isinstance(args["alarm_id"], bool) or \
                    not isinstance(args["alarm_id"], int):
                raise ActionError("alarm_id 须为整数")
            tx.acks.append({"alarm_id": args["alarm_id"]})
            tx.diffs.append(f"消除告警 #{args['alarm_id']}")
            return
        if "device_id" in args:
            device_id = self._find_runtime_device(tx, args.get("device_id"))
            tx.acks.append({"device_id": device_id})
            tx.diffs.append(f"消除设备 {device_id} 的告警")
            return
        raise ActionError("ack_alarm 须提供 alarm_id/device_id/all 之一")

    def _act_set_home(self, tx, args):
        """@brief 默认视角(target/radius/elev/theta 或 reset)"""
        lo.set_home(tx.doc, target=args.get("target"),
                    radius=args.get("radius", "keep"), elev=args.get("elev"),
                    theta=args.get("theta"), reset=bool(args.get("reset")))
        tx.diffs.append("默认视角已更新")

    def _act_reset_layout(self, tx, args):
        """@brief 恢复默认布局(危险,须 confirm)"""
        tx.doc.clear()
        tx.doc.update(lo.default_layout())
        tx.structural = True
        tx.diffs.append("布局已恢复默认模板(1 场区 / 4 栋 / 23 台)")
