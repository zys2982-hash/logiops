"""认证：JWT 签发/校验 + 密码哈希。

注意：bcrypt 锁 4.0.1（4.1+ 与 passlib 1.7.4 存在兼容告警）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except ValueError:
        return False


def create_access_token(user_id: int, email: str, extra: dict[str, Any] | None = None) -> tuple[str, int]:
    settings = get_settings()
    ttl_minutes = settings.access_token_ttl_minutes
    # 注意：JWT 有效期用真实墙钟时间，不用业务时钟 —— 否则 demo 里推进时钟会让 token 立刻过期
    issued_at = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return token, ttl_minutes * 60


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(ErrorCode.AUTH_TOKEN_EXPIRED, "登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise AppError(ErrorCode.AUTH_TOKEN_INVALID, "登录凭证无效") from exc
