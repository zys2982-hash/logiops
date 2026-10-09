"""枚举 → 中文标签（唯一出处）。

为什么需要它：模型会**照抄**我们喂进 prompt 的英文枚举（于是 AI 正文里出现"车辆状态为 REPAIRING"），
后端用 f-string 拼的展示文案也经常直接带英文常量 —— 两者都会**直接给人看**，很影响阅读。
这里集中一份中文说法，AI 事实基线（`services/read_models.py`）与只读工具摘要（`ai/tools/readonly.py`）共用。

约定：
- 未知值**原样返回**（新枚举值不会变成空白）；
- `None` / 空串 → `"—"`；
- 只用于**给人看的文字**；JSON Schema 里的枚举字段（root_cause.code、suggestions[].code 等）仍用英文常量。
"""

from __future__ import annotations

from typing import Any

VEHICLE_STATUS: dict[str, str] = {
    "IDLE": "空闲",
    "IN_TRANSIT": "在途",
    "REPAIRING": "维修中",
    "OFFLINE": "离线",
}

ORDER_STATUS: dict[str, str] = {
    "CREATED": "待派车",
    "DISPATCHED": "已派车",
    "IN_TRANSIT": "在途",
    "DELIVERED": "已送达",
    "CLOSED": "已关闭",
    "CANCELLED": "已取消",
}

EXCEPTION_TYPE: dict[str, str] = {
    "VEHICLE_BREAKDOWN": "车辆故障",
    "DELAY_RISK": "延误风险",
    "OTHER": "其他异常",
}

EXCEPTION_LEVEL: dict[str, str] = {
    "LOW": "低级",
    "MEDIUM": "中级",
    "HIGH": "高级",
    "CRITICAL": "紧急",
}

ROOT_CAUSE: dict[str, str] = {
    "VEHICLE_BREAKDOWN": "车辆故障",
    "TRAFFIC": "交通受阻",
    "WEATHER": "天气原因",
    "CUSTOMS": "关务原因",
    "CUSTOMER": "客户原因",
    "UNKNOWN": "待确认",
}

APPROVAL_ACTION: dict[str, str] = {
    "UPDATE_ETA": "更新预计到达时间",
    "CREATE_FOLLOWUP": "创建跟进任务",
    "SAVE_NOTICE": "保存客户通知",
    "SEND_NOTICE": "发送客户通知",
    "CLOSE_EXCEPTION": "关闭异常",
}

FOLLOWUP_PRIORITY: dict[str, str] = {
    "LOW": "低",
    "NORMAL": "普通",
    "HIGH": "高",
    "URGENT": "紧急",
}

USER_ROLE: dict[str, str] = {
    "OPERATOR": "运营人员",
    "ADMIN": "管理员",
    "VIEWER": "只读用户",
}

SLA_SCOPE: dict[str, str] = {
    "DEFAULT": "默认规则",
    "CUSTOMER_LEVEL": "按客户等级",
    "CUSTOMER": "指定客户",
}

CUSTOMER_LEVEL: dict[str, str] = {
    "NORMAL": "普通客户",
    "VIP": "VIP 客户",
    "SVIP": "SVIP 客户",
}

NOTIFY_PREF: dict[str, str] = {
    "MANUAL_COPY": "人工复制发送",
    "MOCK_EMAIL": "模拟邮件",
    "NONE": "不通知",
}


def label(mapping: dict[str, str], value: Any, default: str = "—") -> str:
    """取中文标签：未知值原样返回，空值给默认。"""
    if value is None or value == "":
        return default
    text = str(value)
    return mapping.get(text, text)
