"""Seed 静态目录：城市里程、用户、客户、承运商、车辆、司机、SLA 规则（§13.2）。

全部数据都是虚构的（客户名/车牌/电话均为假数据，§11.7 合规声明）。
随机种子固定 ``RANDOM_SEED = 20260930``：同样的输入必然产出同样的数据。
"""

from __future__ import annotations

from typing import Any

RANDOM_SEED = 20260930
DEMO_PASSWORD = "Demo@12345"
WORKSPACE_CODE = "SJ"
WORKSPACE_NAME = "顺捷物流"

# --- 用户（4 个角色，密码统一 Demo@12345） ------------------------------------
SEED_USERS: list[dict[str, str]] = [
    {"email": "owner@logiops.dev", "name": "王总", "role": "OWNER", "phone": "13800000101"},
    {"email": "admin@logiops.dev", "name": "李管理", "role": "ADMIN", "phone": "13800000102"},
    {"email": "operator@logiops.dev", "name": "张三", "role": "OPERATOR", "phone": "13800000103"},
    {"email": "viewer@logiops.dev", "name": "访客", "role": "VIEWER", "phone": "13800000104"},
]

# --- 客户（12 个：NORMAL 8 / VIP 3 / SVIP 1） ---------------------------------
SEED_CUSTOMERS: list[dict[str, Any]] = [
    {
        "code": "VIP-01", "name": "远洋集团", "level": "VIP",
        "contact_name": "刘经理", "phone": "13800000001", "email": "liu@yuanyang.example.com",
    },
    {
        "code": "NORM-01", "name": "华北贸易", "level": "NORMAL",
        "contact_name": "陈经理", "phone": "13800000002", "email": "chen@huabei.example.com",
    },
    {
        "code": "NORM-02", "name": "中原物流", "level": "NORMAL",
        "contact_name": "孙主管", "phone": "13800000003", "email": "sun@zhongyuan.example.com",
    },
    {
        "code": "NORM-03", "name": "珠江实业", "level": "NORMAL",
        "contact_name": "周经理", "phone": "13800000004", "email": "zhou@zhujiang.example.com",
    },
    {
        "code": "VIP-02", "name": "中远海运", "level": "VIP",
        "contact_name": "吴总监", "phone": "13800000005", "email": "wu@cosco.example.com",
    },
    {
        "code": "NORM-04", "name": "长江电子", "level": "NORMAL",
        "contact_name": "郑经理", "phone": "13800000006", "email": "zheng@changjiang.example.com",
    },
    {
        "code": "NORM-05", "name": "齐鲁化工", "level": "NORMAL",
        "contact_name": "王主管", "phone": "13800000007", "email": "wang@qilu.example.com",
    },
    {
        "code": "VIP-03", "name": "恒通冷链", "level": "VIP",
        "contact_name": "冯经理", "phone": "13800000008", "email": "feng@hengtong.example.com",
    },
    {
        "code": "NORM-06", "name": "西湖食品", "level": "NORMAL",
        "contact_name": "许经理", "phone": "13800000009", "email": "xu@xihu.example.com",
    },
    {
        "code": "NORM-07", "name": "巴蜀机械", "level": "NORMAL",
        "contact_name": "何主管", "phone": "13800000010", "email": "he@bashu.example.com",
    },
    {
        "code": "NORM-08", "name": "岭南家居", "level": "NORMAL",
        "contact_name": "林经理", "phone": "13800000011", "email": "lin@lingnan.example.com",
    },
    {
        "code": "SVIP-01", "name": "亚太供应链", "level": "SVIP",
        "contact_name": "秦总", "phone": "13800000012", "email": "qin@yatai.example.com",
    },
]

# --- 承运商（4 个） -----------------------------------------------------------
SEED_CARRIERS: list[dict[str, str]] = [
    {"code": "CR-01", "name": "顺达运输", "contact_name": "赵队长", "phone": "13900000001"},
    {"code": "CR-02", "name": "中通快运", "contact_name": "钱队长", "phone": "13900000002"},
    {"code": "CR-03", "name": "德邦物流", "contact_name": "孙队长", "phone": "13900000003"},
    {"code": "CR-04", "name": "京东物流", "contact_name": "李队长", "phone": "13900000004"},
]

# --- 司机（24 名） -----------------------------------------------------------
DRIVER_NAMES: list[str] = [
    "李四", "张伟", "王强", "刘洋", "陈刚", "杨帆", "赵磊", "黄鹏",
    "周涛", "吴斌", "徐亮", "孙浩", "马超", "朱峰", "胡军", "郭鹏程",
    "何伟", "高翔", "林涛", "罗鑫", "郑凯", "梁爽", "谢超", "唐磊",
]

# --- 车辆（24 台；第 1 台是 CASE-A 的主角） -----------------------------------
VEHICLE_TYPES: list[tuple[str, float]] = [
    ("9.6米厢车", 18.0),
    ("13米平板", 30.0),
    ("冷藏车", 15.0),
    ("17.5米挂车", 25.0),
]
PLATE_PREFIXES: list[str] = ["津A", "沪B", "京C", "鲁A", "苏E", "浙A"]

# --- SLA 规则（用户口径 2026-10-06：只保留「VIP 客户等级规则」+「默认规则」两种）---
# 「指定客户（VIP-01 专属）」已下线：界面不再提供该作用域，接口创建 CUSTOMER 作用域会被 422 拦掉。
SEED_SLA_RULES: list[dict[str, Any]] = [
    {
        "name": "默认规则", "scope_type": "DEFAULT", "scope_value": None,
        "deadline_offset_hours": 30, "max_delay_minutes": 30, "priority": 100,
        "description": "发车后 30 小时承诺到达，允许延迟 30 分钟（兜底：非 VIP 客户）",
    },
    {
        "name": "VIP 客户规则", "scope_type": "CUSTOMER_LEVEL", "scope_value": "VIP",
        "deadline_offset_hours": 24, "max_delay_minutes": 0, "priority": 10,
        "description": "VIP 客户发车后 24 小时承诺到达，不允许延迟",
    },
]

CARGO_DESCS: list[str] = [
    "家电整机", "汽车配件", "快消品", "钢材卷板", "冷藏食品", "电子元器件",
    "建材瓷砖", "服装百货", "化工原料", "机械设备", "医药冷链", "家具板材",
]

# 轨迹时间轴的位置占比（0 = 发车，1 = 最后一条事件）
EVENT_FRACTIONS: list[float] = [0.0, 0.12, 0.22, 0.32, 0.42, 0.56, 0.68, 0.8, 1.0]

# --- 城市坐标（轨迹 lat/lng 用，可选） ---------------------------------------
CITY_COORDS: dict[str, tuple[float, float]] = {
    "天津": (39.3434, 117.3616),
    "上海": (31.2304, 121.4737),
    "北京": (39.9042, 116.4074),
    "济南": (36.6512, 117.1201),
    "南京": (32.0603, 118.7969),
    "郑州": (34.7466, 113.6254),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551),
    "青岛": (36.0671, 120.3826),
    "武汉": (30.5928, 114.3055),
    "成都": (30.5728, 104.0668),
    "西安": (34.3416, 108.9398),
    "石家庄": (38.0428, 114.5149),
    "滨州": (37.3835, 117.9709),
    "淄博": (36.8131, 118.0548),
    "潍坊": (36.7069, 119.1618),
    "东莞": (23.0207, 113.7518),
    "湖州": (30.8931, 120.0862),
    "合肥": (31.8206, 117.2272),
    "汉中": (33.0677, 107.0231),
    "德州": (37.4355, 116.3575),
}

# --- 预设里程表：起点 → 终点 的途经点与累计公里（§8.5 行程比例估算） -----------
ROUTE_PLANS: list[dict[str, Any]] = [
    {
        "origin": "天津", "dest": "上海",
        "waypoints": [("天津", 0), ("滨州", 220), ("济南", 400), ("南京", 690), ("上海", 800)],
    },
    {
        "origin": "上海", "dest": "北京",
        "waypoints": [("上海", 0), ("南京", 300), ("济南", 700), ("德州", 900), ("北京", 1200)],
    },
    {"origin": "北京", "dest": "郑州", "waypoints": [("北京", 0), ("石家庄", 280), ("郑州", 700)]},
    {"origin": "广州", "dest": "深圳", "waypoints": [("广州", 0), ("东莞", 70), ("深圳", 140)]},
    {"origin": "杭州", "dest": "南京", "waypoints": [("杭州", 0), ("湖州", 90), ("南京", 280)]},
    {"origin": "青岛", "dest": "济南", "waypoints": [("青岛", 0), ("潍坊", 180), ("济南", 350)]},
    {"origin": "武汉", "dest": "上海", "waypoints": [("武汉", 0), ("合肥", 400), ("南京", 700), ("上海", 1000)]},
    {"origin": "成都", "dest": "西安", "waypoints": [("成都", 0), ("汉中", 450), ("西安", 750)]},
]

CASE_A_ROUTE = {"origin": "天津", "dest": "上海"}
CASE_A_ORDER_NO = "SO20260930021"
CASE_A_PLATE = "津A·12345"
CASE_A_TOTAL_KM = 800
CASE_A_TRAVELED_KM = 400  # 已抵达济南（里程表：天津 0 → 济南 400）
CASE_A_TARGET_DELAY_MINUTES = 270
CASE_A_STALL_MINUTES = 180
CASE_A_RECOVERY_LOCAL_HOUR = 20  # 承运商原文"预计晚上 8 点恢复"


def route_plan(origin: str, dest: str) -> dict[str, Any]:
    for plan in ROUTE_PLANS:
        if plan["origin"] == origin and plan["dest"] == dest:
            return plan
    return {"origin": origin, "dest": dest, "waypoints": [(origin, 0), (dest, 800)]}


def route_total_km(plan: dict[str, Any]) -> int:
    return int(plan["waypoints"][-1][1])


def mileage_of(plan: dict[str, Any], city: str) -> int | None:
    for name, km in plan["waypoints"]:
        if name == city:
            return int(km)
    return None


def progress_ratio(plan: dict[str, Any], city: str | None, fallback: float = 0.6) -> float:
    """已完成行程比例；城市不在预设里程表时用 0.6 兜底（§8.5）。"""
    total = route_total_km(plan)
    if not city or total <= 0:
        return fallback
    km = mileage_of(plan, city)
    if km is None:
        return fallback
    return min(max(km / total, 0.0), 1.0)


def build_drivers() -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    for index, name in enumerate(DRIVER_NAMES):
        status = "AVAILABLE"
        if index % 6 == 1:
            status = "ON_TRIP"
        elif index % 6 == 4:
            status = "OFF_DUTY"
        drivers.append(
            {
                "name": name,
                "phone": f"1370000{index:04d}",
                "carrier_index": index % len(SEED_CARRIERS),
                "license_no": f"A2{20260000 + index}",
                "status": status,
            }
        )
    drivers[0]["status"] = "ON_TRIP"  # 李四：CASE-A 的司机
    return drivers


def build_vehicles() -> list[dict[str, Any]]:
    vehicles: list[dict[str, Any]] = []
    for index in range(24):
        prefix = PLATE_PREFIXES[index % len(PLATE_PREFIXES)]
        plate = CASE_A_PLATE if index == 0 else f"{prefix}·{12346 + index:05d}"
        vehicle_type, capacity = VEHICLE_TYPES[index % len(VEHICLE_TYPES)]
        status = "IDLE"
        if index % 4 == 1:
            status = "IN_TRANSIT"
        elif index % 4 == 3:
            status = "OFFLINE"
        vehicles.append(
            {
                "plate_no": plate,
                "vehicle_type": vehicle_type,
                "capacity_ton": capacity,
                "carrier_index": index % len(SEED_CARRIERS),
                "driver_index": index,
                "status": status,
                "current_city": None,
                "remark": None,
            }
        )
    # CASE-A 主演：津A·12345 在济南维修
    vehicles[0].update({"status": "REPAIRING", "current_city": "济南", "remark": "济南爆胎，待修理厂维修"})
    return vehicles


__all__ = [
    "CARGO_DESCS",
    "CASE_A_ORDER_NO",
    "CASE_A_PLATE",
    "CASE_A_RECOVERY_LOCAL_HOUR",
    "CASE_A_ROUTE",
    "CASE_A_STALL_MINUTES",
    "CASE_A_TARGET_DELAY_MINUTES",
    "CASE_A_TOTAL_KM",
    "CASE_A_TRAVELED_KM",
    "CITY_COORDS",
    "DEMO_PASSWORD",
    "DRIVER_NAMES",
    "EVENT_FRACTIONS",
    "PLATE_PREFIXES",
    "RANDOM_SEED",
    "ROUTE_PLANS",
    "SEED_CARRIERS",
    "SEED_CUSTOMERS",
    "SEED_SLA_RULES",
    "SEED_USERS",
    "VEHICLE_TYPES",
    "WORKSPACE_CODE",
    "WORKSPACE_NAME",
    "build_drivers",
    "build_vehicles",
    "mileage_of",
    "progress_ratio",
    "route_plan",
    "route_total_km",
]
