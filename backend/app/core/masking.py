"""脱敏：写入 LLM prompt 与对外响应前必须过这里（基线文档 §8.7）。"""

from __future__ import annotations

import re

_PHONE_RE = re.compile(r"(?<!\d)(\d{3})\d{4}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"([\w.+-])([\w.+-]*)@([\w-]+\.[\w.-]+)")


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    return _PHONE_RE.sub(r"\1****\2", value)


def mask_email(value: str | None) -> str | None:
    if not value:
        return value
    return _EMAIL_RE.sub(r"\1***@\3", value)


def mask_text(text: str | None) -> str | None:
    if not text:
        return text
    return mask_email(mask_phone(text))


def mask_record(data: dict, fields: tuple[str, ...] = ("contact_phone", "phone", "contact_email", "email")) -> dict:
    """浅层脱敏：常见联系方式字段。"""
    result = dict(data)
    for field in fields:
        if result.get(field):
            if "phone" in field:
                result[field] = mask_phone(str(result[field]))
            elif "email" in field:
                result[field] = mask_email(str(result[field]))
    return result
