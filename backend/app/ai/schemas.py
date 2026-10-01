"""AI 输出的 Pydantic Schema（严格按基线文档 §11.2 表 2，全部 additionalProperties=false）。

三张表里 "是否用于写库" 为 "是" 的字段只是"建议"，最终写库一律走人工审批 + Service。
本模块只负责结构与字面约束；跨字段/跨事实的一致性在 guard.py。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageStatus = Literal["REPAIRING", "WAITING_PARTS", "BREAKDOWN", "MOVING", "UNKNOWN"]
T1ExceptionType = Literal["VEHICLE_BREAKDOWN", "DELAY_RISK", "OTHER"]
RootCauseCode = Literal["VEHICLE_BREAKDOWN", "TRAFFIC", "WEATHER", "CUSTOMS", "CUSTOMER", "UNKNOWN"]
ApprovalActionCode = Literal[
    "UPDATE_ETA",
    "CREATE_FOLLOWUP",
    "SAVE_NOTICE",
    "SEND_NOTICE",
    "CLOSE_EXCEPTION",
]
EvidenceType = Literal[
    "ORDER",
    "TRACKING_EVENT",
    "CUSTOMER",
    "SLA_RULE",
    "VEHICLE",
    "EXCEPTION",
    "CARRIER_MESSAGE",
    "KNOWLEDGE_CHUNK",
]
NoticeTone = Literal["FORMAL", "APOLOGETIC"]


class StrictModel(BaseModel):
    """所有 AI 输出模型：拒绝未声明字段（防模型自由发挥）。"""

    model_config = ConfigDict(extra="forbid")


# --- T1 PARSE_MESSAGE --------------------------------------------------------
class ParseMessageOutput(StrictModel):
    exception_type: T1ExceptionType
    location: str = Field(min_length=1, max_length=64)
    status: MessageStatus
    estimated_recovery_at: datetime | None
    confidence: float = Field(ge=0, le=1)
    missing_info: list[str]


# --- T2 ANALYZE_EXCEPTION ----------------------------------------------------
class RootCause(StrictModel):
    code: RootCauseCode
    note: str = Field(min_length=1, max_length=200)


class Impact(StrictModel):
    delay_minutes: int = Field(ge=-1440, le=100000)
    sla_breached: bool
    affected_customer_level: str | None = Field(default=None, max_length=16)


class Suggestion(StrictModel):
    code: ApprovalActionCode
    title: str = Field(min_length=1, max_length=120)
    rationale: str | None = Field(default=None, max_length=300)
    assignee_role: str | None = Field(default=None, max_length=16)


class EvidenceRef(StrictModel):
    type: EvidenceType
    id: int | str
    note: str | None = Field(default=None, max_length=200)
    doc: str | None = Field(default=None, max_length=128)
    section: str | None = Field(default=None, max_length=128)


class AnalysisOutput(StrictModel):
    summary: str = Field(min_length=1, max_length=300)
    root_cause: RootCause
    impact: Impact
    suggestions: list[Suggestion] = Field(min_length=1, max_length=5)
    open_questions: list[str] = Field(max_length=5)
    evidence_refs: list[EvidenceRef]


# --- T3 DRAFT_NOTICE ---------------------------------------------------------
class NoticeOutput(StrictModel):
    subject: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=500)
    tone: NoticeTone


TASK_SCHEMAS: dict[str, type[StrictModel]] = {
    "PARSE_MESSAGE": ParseMessageOutput,
    "ANALYZE_EXCEPTION": AnalysisOutput,
    "DRAFT_NOTICE": NoticeOutput,
}

__all__ = [
    "TASK_SCHEMAS",
    "AnalysisOutput",
    "EvidenceRef",
    "Impact",
    "NoticeOutput",
    "ParseMessageOutput",
    "RootCause",
    "StrictModel",
    "Suggestion",
]
