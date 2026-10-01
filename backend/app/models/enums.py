"""全局枚举字典（基线文档 §7.2）。

统一用 StrEnum：值即 API/DB 中的字符串，便于 JSON 序列化与断言。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class MemberStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    REMOVED = "REMOVED"


class CustomerLevel(StrEnum):
    NORMAL = "NORMAL"
    VIP = "VIP"
    SVIP = "SVIP"


class CarrierStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class VehicleStatus(StrEnum):
    IDLE = "IDLE"
    IN_TRANSIT = "IN_TRANSIT"
    REPAIRING = "REPAIRING"
    OFFLINE = "OFFLINE"


class DriverStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ON_TRIP = "ON_TRIP"
    OFF_DUTY = "OFF_DUTY"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class TrackingEventType(StrEnum):
    DEPART = "DEPART"
    ARRIVE = "ARRIVE"
    STOP = "STOP"
    RESUME = "RESUME"
    REPAIR_START = "REPAIR_START"
    REPAIR_END = "REPAIR_END"
    DELIVER = "DELIVER"
    NOTE = "NOTE"


class TrackingSource(StrEnum):
    MOCK = "MOCK"
    DRIVER = "DRIVER"
    CARRIER = "CARRIER"
    OPERATOR = "OPERATOR"
    SYSTEM = "SYSTEM"


class SlaScopeType(StrEnum):
    DEFAULT = "DEFAULT"
    CUSTOMER_LEVEL = "CUSTOMER_LEVEL"
    CUSTOMER = "CUSTOMER"


class ExceptionType(StrEnum):
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    DELAY_RISK = "DELAY_RISK"


class ExceptionLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionStatus(StrEnum):
    DETECTED = "DETECTED"
    CONFIRMING = "CONFIRMING"
    ANALYZING = "ANALYZING"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


OPEN_EXCEPTION_STATUSES: frozenset[ExceptionStatus] = frozenset(
    {
        ExceptionStatus.DETECTED,
        ExceptionStatus.CONFIRMING,
        ExceptionStatus.ANALYZING,
        ExceptionStatus.PROCESSING,
        ExceptionStatus.RESOLVED,
    }
)


class ExceptionEventType(StrEnum):
    DETECTED = "DETECTED"
    MESSAGE_ADDED = "MESSAGE_ADDED"
    CONFIRMED = "CONFIRMED"
    ANALYSIS_REQUESTED = "ANALYSIS_REQUESTED"
    ANALYSIS_READY = "ANALYSIS_READY"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    EXECUTE_FAILED = "EXECUTE_FAILED"
    ETA_UPDATED = "ETA_UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    COMMENT = "COMMENT"
    FOLLOWUP_DONE = "FOLLOWUP_DONE"
    CLOSED = "CLOSED"


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AI = "AI"


class MessageChannel(StrEnum):
    MANUAL_PASTE = "MANUAL_PASTE"
    MOCK_WECHAT = "MOCK_WECHAT"
    MOCK_SMS = "MOCK_SMS"


class ParseStatus(StrEnum):
    PENDING = "PENDING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class AnalysisTaskType(StrEnum):
    PARSE_MESSAGE = "PARSE_MESSAGE"
    ANALYZE_EXCEPTION = "ANALYZE_EXCEPTION"
    DRAFT_NOTICE = "DRAFT_NOTICE"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class StepType(StrEnum):
    LLM = "LLM"
    TOOL = "TOOL"
    VALIDATE = "VALIDATE"


class StepStatus(StrEnum):
    RUNNING = "RUNNING"
    OK = "OK"
    ERROR = "ERROR"


class ApprovalAction(StrEnum):
    UPDATE_ETA = "UPDATE_ETA"
    CREATE_FOLLOWUP = "CREATE_FOLLOWUP"
    SAVE_NOTICE = "SAVE_NOTICE"
    SEND_NOTICE = "SEND_NOTICE"
    CLOSE_EXCEPTION = "CLOSE_EXCEPTION"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class FollowupStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class FollowupSource(StrEnum):
    AI_SUGGESTED = "AI_SUGGESTED"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class NotificationChannel(StrEnum):
    MANUAL_COPY = "MANUAL_COPY"
    MOCK_EMAIL = "MOCK_EMAIL"


class NotificationStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SENT_MOCK = "SENT_MOCK"
    SKIPPED = "SKIPPED"


class DetectionRule(StrEnum):
    STALL_OVER_THRESHOLD = "STALL_OVER_THRESHOLD"
    ETA_BREACH_SLA = "ETA_BREACH_SLA"
    MANUAL = "MANUAL"


class AuditSource(StrEnum):
    MANUAL = "MANUAL"
    APPROVED_AI = "APPROVED_AI"
    SYSTEM = "SYSTEM"


class RootCauseCode(StrEnum):
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    TRAFFIC = "TRAFFIC"
    WEATHER = "WEATHER"
    CUSTOMS = "CUSTOMS"
    CUSTOMER = "CUSTOMER"
    UNKNOWN = "UNKNOWN"


class KnowledgeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class MessageParseStatusValue(StrEnum):
    """承运商消息中车辆状态（LLM 输出枚举，见表 2）。"""

    REPAIRING = "REPAIRING"
    WAITING_PARTS = "WAITING_PARTS"
    BREAKDOWN = "BREAKDOWN"
    MOVING = "MOVING"
    UNKNOWN = "UNKNOWN"
