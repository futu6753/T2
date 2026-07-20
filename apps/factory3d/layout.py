# -*- coding: utf-8 -*-
"""
@file    layout.py
@brief   三维大屏布局模型(L03 §3/§4/§7):布局单例整体 JSON 存储(迁移 v7
         f3d_layout),结构变更 data_rev+1;程序化默认模板(1 场区/4 栋/23 台);
         全部结构操作为纯函数(供 REST/编辑台/AI 助手三方共用,助手原子事务
         在深拷贝上执行后单次落库,13-R-F3D-2)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import copy
import datetime
import json
import secrets

BUILDING_TYPES = ("assembly", "warehouse", "power", "office")
DEVICE_TYPES = ("plc", "sensor", "camera", "power", "hvac")
ZONE_BOUND = 160            # 场区平面半径(米):楼宇偏移夹取边界(L03 §4)
ELEV_MIN, ELEV_MAX = 10, 80


class LayoutError(ValueError):
    """布局操作违规(重叠/越界/对象不存在等,人话报错 400)。"""


def _did() -> str:
    """@brief 生成新对象 id"""
    return secrets.token_hex(4)


def _device(name: str, dtype: str, room: str, ip: str, pos: list) -> dict:
    """@brief 构造设备记录(字段表沿 L03 §7 数据模型)"""
    return {"id": _did(), "name": name, "type": dtype, "icon": None,
            "show": True, "label": False, "room": room, "ip": ip,
            "pos": pos, "seed": {}}


def _default_buildings() -> list:
    """@brief 默认四栋厂房与 21 台楼内设备(总装 7/仓库 5/动力 5/办公 4)"""
    spec = [
        ("总装车间", "assembly", {"dx": -60, "dz": -30}, {"w": 44, "d": 26}, [
            ("总装-PLC-01", "plc", "一层产线", "10.10.1.11"),
            ("总装-PLC-02", "plc", "一层产线", "10.10.1.12"),
            ("总装-温湿度-01", "sensor", "一层产线", "10.10.1.21"),
            ("总装-温湿度-02", "sensor", "二层装配", "10.10.1.22"),
            ("总装-摄像-01", "camera", "一层产线", "10.10.1.31"),
            ("总装-摄像-02", "camera", "二层装配", "10.10.1.32"),
            ("总装-配电-01", "power", "配电间", "10.10.1.41")]),
        ("立体仓库", "warehouse", {"dx": 55, "dz": -35}, {"w": 36, "d": 24}, [
            ("仓库-摄像-01", "camera", "入库口", "10.10.2.31"),
            ("仓库-摄像-02", "camera", "拣选区", "10.10.2.32"),
            ("仓库-温湿度-01", "sensor", "高架区", "10.10.2.21"),
            ("仓库-温湿度-02", "sensor", "冷藏区", "10.10.2.22"),
            ("仓库-PLC-01", "plc", "堆垛机房", "10.10.2.11")]),
        ("动力站", "power", {"dx": -50, "dz": 45}, {"w": 30, "d": 20}, [
            ("动力-配电-01", "power", "高压室", "10.10.3.41"),
            ("动力-配电-02", "power", "低压室", "10.10.3.42"),
            ("动力-温度-01", "sensor", "变压器区", "10.10.3.21"),
            ("动力-温度-02", "sensor", "电容室", "10.10.3.22"),
            ("动力-空调-01", "hvac", "值班室", "10.10.3.51")]),
        ("综合办公楼", "office", {"dx": 50, "dz": 45}, {"w": 28, "d": 18}, [
            ("办公-空调-01", "hvac", "三层机房", "10.10.4.51"),
            ("办公-空调-02", "hvac", "大堂", "10.10.4.52"),
            ("办公-摄像-01", "camera", "大堂", "10.10.4.31"),
            ("办公-烟感-01", "sensor", "三层机房", "10.10.4.21")]),
    ]
    buildings = []
    for name, btype, offset, size, devices in spec:
        buildings.append({
            "id": _did(), "name": name, "type": btype, "model": None,
            "offset": dict(offset), "size": dict(size),
            "custom_devices": False,
            "devices": [_device(dn, dt, room, ip, [0, 0, 0])
                        for dn, dt, room, ip in devices]})
    return buildings


def default_layout() -> dict:
    """@brief 程序化默认布局:1 场区 / 4 栋 / 21 台楼内 + 2 台室外 = 23 台"""
    return {
        "version": 3,
        "home": {"target": [0, 0, 0], "radius": None, "elev": 55, "theta": 35},
        "zones": [{"id": _did(), "name": "云枢主场区",
                   "focus": {"elev": 40, "theta": None},
                   "center": {"dx": 0, "dz": 0},
                   "buildings": _default_buildings()}],
        "outdoor": [
            _device("园区-大门摄像-01", "camera", "南大门", "10.10.9.31",
                    [0, 4, 95]),
            _device("园区-路灯配电-01", "power", "环路", "10.10.9.41",
                    [-80, 0, 60])],
    }


# ---- 查找 -----------------------------------------------------------------
def find_zone(doc: dict, zone_id: str) -> dict:
    """@brief 按 id 找场区;不存在抛 LayoutError"""
    for zone in doc["zones"]:
        if zone["id"] == zone_id:
            return zone
    raise LayoutError(f"场区不存在: {zone_id}")


def find_building(doc: dict, building_id: str) -> tuple:
    """@brief 按 id 找楼宇 @return (zone, building)"""
    for zone in doc["zones"]:
        for building in zone["buildings"]:
            if building["id"] == building_id:
                return zone, building
    raise LayoutError(f"楼宇不存在: {building_id}")


def find_device(doc: dict, device_id: str) -> tuple:
    """@brief 按 id 找设备 @return (building|None, device);室外设备楼宇为 None"""
    for zone in doc["zones"]:
        for building in zone["buildings"]:
            for device in building["devices"]:
                if device["id"] == device_id:
                    return building, device
    for device in doc["outdoor"]:
        if device["id"] == device_id:
            return None, device
    raise LayoutError(f"设备不存在: {device_id}")


def iter_devices(doc: dict):
    """@brief 遍历全部设备 @return 生成 (building|None, device)"""
    for zone in doc["zones"]:
        for building in zone["buildings"]:
            for device in building["devices"]:
                yield building, device
    for device in doc["outdoor"]:
        yield None, device


def count_devices(doc: dict) -> int:
    """@brief 全场设备数(healthz/KPI 用)"""
    return sum(1 for _ in iter_devices(doc))


# ---- 校验 -----------------------------------------------------------------
def _rects_overlap(one: dict, two: dict) -> bool:
    """@brief 楼宇轴对齐矩形重叠判定(严格正面积才算重叠)"""
    ax, az = one["offset"]["dx"], one["offset"]["dz"]
    bx, bz = two["offset"]["dx"], two["offset"]["dz"]
    return (abs(ax - bx) * 2 < one["size"]["w"] + two["size"]["w"]
            and abs(az - bz) * 2 < one["size"]["d"] + two["size"]["d"])


def validate_layout(doc: dict):
    """@brief 整体校验:同场区楼宇不重叠、偏移在场区边界内(L03 §4)"""
    for zone in doc["zones"]:
        buildings = zone["buildings"]
        for building in buildings:
            for axis in ("dx", "dz"):
                if abs(building["offset"][axis]) > ZONE_BOUND:
                    raise LayoutError(
                        f"楼宇「{building['name']}」越出场区边界(±{ZONE_BOUND} 米)")
        for index, one in enumerate(buildings):
            for two in buildings[index + 1:]:
                if _rects_overlap(one, two):
                    raise LayoutError(
                        f"楼宇「{one['name']}」与「{two['name']}」平面重叠")


# ---- 结构操作(纯函数,三方共用) -----------------------------------------
def add_zone(doc: dict, name: str) -> dict:
    """@brief 新增场区"""
    if not name or len(name) > 40:
        raise LayoutError("场区名称须为 1~40 字")
    zone = {"id": _did(), "name": name, "focus": {"elev": 40, "theta": None},
            "center": {"dx": 0, "dz": 0}, "buildings": []}
    doc["zones"].append(zone)
    return zone


def remove_zone(doc: dict, zone_id: str):
    """@brief 删除场区(连带楼宇与设备,L03 §3.8 confirm 语义在助手层)"""
    zone = find_zone(doc, zone_id)
    doc["zones"].remove(zone)


def set_zone_focus(doc: dict, zone_id: str, elev=None, theta="keep"):
    """@brief 场区透视切入角:elev 10~80;theta=None 表示跟随视角"""
    zone = find_zone(doc, zone_id)
    if elev is not None:
        if not ELEV_MIN <= float(elev) <= ELEV_MAX:
            raise LayoutError(f"俯仰角须在 {ELEV_MIN}~{ELEV_MAX} 度之间")
        zone["focus"]["elev"] = float(elev)
    if theta != "keep":
        zone["focus"]["theta"] = None if theta is None else float(theta)


def set_zone_center(doc: dict, zone_id: str, dx=None, dz=None,
                    reset: bool = False):
    """@brief 场区旋转中心偏移(米);reset=恢复 0,0"""
    zone = find_zone(doc, zone_id)
    if reset:
        zone["center"] = {"dx": 0, "dz": 0}
        return
    if dx is not None:
        zone["center"]["dx"] = float(dx)
    if dz is not None:
        zone["center"]["dz"] = float(dz)


def add_building(doc: dict, zone_id: str, name: str,
                 btype: str = "warehouse") -> dict:
    """@brief 新增楼宇(自动找不重叠空位,找不到报错)"""
    zone = find_zone(doc, zone_id)
    if btype not in BUILDING_TYPES:
        raise LayoutError(f"楼宇类型非法: {btype},可选 {BUILDING_TYPES}")
    if not name or len(name) > 40:
        raise LayoutError("楼宇名称须为 1~40 字")
    building = {"id": _did(), "name": name, "type": btype, "model": None,
                "offset": {"dx": 0, "dz": 0}, "size": {"w": 30, "d": 20},
                "custom_devices": False, "devices": []}
    for dx in range(0, ZONE_BOUND, 20):
        for candidate in ({"dx": dx, "dz": 90}, {"dx": -dx, "dz": 90},
                          {"dx": dx, "dz": -90}, {"dx": -dx, "dz": -90}):
            building["offset"] = dict(candidate)
            if not any(_rects_overlap(building, other)
                       for other in zone["buildings"]):
                zone["buildings"].append(building)
                return building
    raise LayoutError("场区内已无不重叠空位可放置新楼宇")


def rename_building(doc: dict, building_id: str, name: str):
    """@brief 楼宇改名"""
    if not name or len(name) > 40:
        raise LayoutError("楼宇名称须为 1~40 字")
    find_building(doc, building_id)[1]["name"] = name


def set_building_type(doc: dict, building_id: str, btype: str):
    """@brief 改楼宇外观类型"""
    if btype not in BUILDING_TYPES:
        raise LayoutError(f"楼宇类型非法: {btype},可选 {BUILDING_TYPES}")
    find_building(doc, building_id)[1]["type"] = btype


def set_building_model(doc: dict, building_id: str, model):
    """@brief 绑定外观模型(null=回退程序化外观,L03 §3.5)"""
    find_building(doc, building_id)[1]["model"] = model


def move_building(doc: dict, building_id: str, dx: float, dz: float):
    """@brief 移动楼宇:夹取进场区边界 + 同场区重叠校验,失败抛错(回弹语义)"""
    zone, building = find_building(doc, building_id)
    moved = copy.deepcopy(building)
    moved["offset"] = {"dx": max(-ZONE_BOUND, min(ZONE_BOUND, float(dx))),
                       "dz": max(-ZONE_BOUND, min(ZONE_BOUND, float(dz)))}
    for other in zone["buildings"]:
        if other["id"] != building_id and _rects_overlap(moved, other):
            raise LayoutError(
                f"移动后与楼宇「{other['name']}」重叠,已回弹")
    building["offset"] = moved["offset"]


def remove_building(doc: dict, building_id: str):
    """@brief 删除楼宇(连带设备)"""
    zone, building = find_building(doc, building_id)
    zone["buildings"].remove(building)


def add_device(doc: dict, name: str, dtype: str, building_id: str = None,
               room: str = "", ip: str = "", pos: list = None) -> dict:
    """@brief 新增设备(building_id=None 为室外);脱模板置 custom_devices"""
    if dtype not in DEVICE_TYPES:
        raise LayoutError(f"设备类型非法: {dtype},可选 {DEVICE_TYPES}")
    if not name or len(name) > 60:
        raise LayoutError("设备名称须为 1~60 字")
    device = _device(name, dtype, room, ip, pos or [0, 0, 0])
    if building_id is None:
        doc["outdoor"].append(device)
    else:
        building = find_building(doc, building_id)[1]
        building["devices"].append(device)
        building["custom_devices"] = True
    return device


_PATCHABLE = ("name", "room", "ip", "type", "pos", "icon", "show", "label",
              "seed")


def patch_device(doc: dict, device_id: str, fields: dict):
    """@brief 改设备字段(白名单外的键报错,B7 防越权写)"""
    building, device = find_device(doc, device_id)
    unknown = sorted(set(fields) - set(_PATCHABLE))
    if unknown:
        raise LayoutError(f"设备不支持的字段: {unknown}")
    if "type" in fields and fields["type"] not in DEVICE_TYPES:
        raise LayoutError(f"设备类型非法: {fields['type']}")
    if "name" in fields and (not fields["name"] or len(fields["name"]) > 60):
        raise LayoutError("设备名称须为 1~60 字")
    device.update(fields)
    if building is not None:
        building["custom_devices"] = True


def remove_device(doc: dict, device_id: str):
    """@brief 删除设备"""
    building, device = find_device(doc, device_id)
    if building is None:
        doc["outdoor"].remove(device)
    else:
        building["devices"].remove(device)
        building["custom_devices"] = True


def reset_building_devices(doc: dict, building_id: str):
    """@brief 按模板重置楼宇设备清单(L03 §3.8)"""
    zone, building = find_building(doc, building_id)
    for template in default_layout()["zones"][0]["buildings"]:
        if template["name"] == building["name"]:
            building["devices"] = template["devices"]
            building["custom_devices"] = False
            return
    building["devices"] = []
    building["custom_devices"] = False


def set_home(doc: dict, target=None, radius="keep", elev=None, theta=None,
             reset: bool = False):
    """@brief 默认视角:target/radius/elev/theta 或 reset(L03 §3.7)"""
    if reset:
        doc["home"] = default_layout()["home"]
        return
    home = doc["home"]
    if target is not None:
        if not (isinstance(target, (list, tuple)) and len(target) == 3):
            raise LayoutError("target 须为 [x,y,z] 三元数组")
        home["target"] = [float(value) for value in target]
    if radius != "keep":
        home["radius"] = None if radius is None else float(radius)
    if elev is not None:
        if not ELEV_MIN <= float(elev) <= ELEV_MAX:
            raise LayoutError(f"俯仰角须在 {ELEV_MIN}~{ELEV_MAX} 度之间")
        home["elev"] = float(elev)
    if theta is not None:
        home["theta"] = float(theta)


# ---- 持久层 ----------------------------------------------------------------
class LayoutService:
    """布局单例持久化:整体 JSON + data_rev(结构变更 +1,广播依据)。"""

    def __init__(self, db):
        """@brief 绑定库并确保默认布局就位"""
        self._db = db
        if not self._db.query("SELECT id FROM f3d_layout WHERE id = 1"):
            self._persist(default_layout(), 0)

    def _persist(self, doc: dict, data_rev: int):
        """@brief 落库(insert-or-update 单行)"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = json.dumps(doc, ensure_ascii=False)
        if self._db.query("SELECT id FROM f3d_layout WHERE id = 1"):
            self._db.execute(
                "UPDATE f3d_layout SET doc = ?, data_rev = ?, updated_at = ?"
                " WHERE id = 1", (payload, data_rev, now))
        else:
            self._db.execute(
                "INSERT INTO f3d_layout(id, doc, data_rev, updated_at)"
                " VALUES(1, ?, ?, ?)", (payload, data_rev, now))

    def get(self) -> tuple:
        """@brief 读取 @return (doc, data_rev)"""
        row = self._db.query(
            "SELECT doc, data_rev FROM f3d_layout WHERE id = 1")[0]
        return json.loads(row[0]), int(row[1])

    def mutate(self, mutator, structural: bool = True) -> tuple:
        """
        @brief  在深拷贝上执行 mutator → 整体校验 → 落库(违规不落任何变更)
        @return (doc, data_rev)
        """
        doc, data_rev = self.get()
        draft = copy.deepcopy(doc)
        mutator(draft)
        validate_layout(draft)
        data_rev = data_rev + 1 if structural else data_rev
        self._persist(draft, data_rev)
        return draft, data_rev

    def replace(self, doc: dict, structural: bool = True) -> tuple:
        """@brief 助手原子提交:整篇替换,单次 data_rev+1(13-R-F3D-2)"""
        validate_layout(doc)
        _, data_rev = self.get()
        data_rev = data_rev + 1 if structural else data_rev
        self._persist(doc, data_rev)
        return doc, data_rev
