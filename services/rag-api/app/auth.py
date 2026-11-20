import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException

from .config import settings

ROLE_CLAIMS = ("role", "roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")
PERM_CLAIMS = ("permission", "permissions")
ID_CLAIMS = ("sub", "nameid", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
             "unique_name", "name")


@dataclass
class User:
    id: str
    name: str
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    jti: str = ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _collect(payload: dict, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for k in keys:
        for v in _as_list(payload.get(k)):
            if v not in out:
                out.append(v)
    return out


def decode_token(token: str) -> User:
    kwargs: dict[str, Any] = {"algorithms": ["HS256"], "leeway": 30}
    options: dict[str, Any] = {"require": ["exp"]}
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    else:
        options["verify_aud"] = False
    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer
    kwargs["options"] = options
    payload = jwt.decode(token, settings.jwt_secret, **kwargs)

    uid = ""
    for k in ID_CLAIMS:
        if payload.get(k):
            uid = str(payload[k])
            break
    surname = str(payload.get("surname", "") or "")
    given = str(payload.get("name", "") or "")
    display = (surname + " " + given).strip() or uid
    return User(
        id=uid or "unknown",
        name=display,
        roles=_collect(payload, ROLE_CLAIMS),
        permissions=_collect(payload, PERM_CLAIMS),
        jti=str(payload.get("jti", "")),
    )


_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(user_id: str) -> None:
    now = time.time()
    q = _hits[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.rate_limit_per_min:
        raise HTTPException(429, "Bạn gửi quá nhanh, vui lòng thử lại sau ít giây.")
    q.append(now)


def get_user(authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Thiếu Bearer token")
    try:
        user = decode_token(authorization[7:].strip())
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token đã hết hạn")
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Token không hợp lệ: {e}")
    return user


def get_chat_user(user: User = Depends(get_user)) -> User:
    _rate_limit(user.id)
    return user


def is_kb_admin(user: User) -> bool:
    if settings.kb_admin_permission and settings.kb_admin_permission in user.permissions:
        return True
    return any(r in user.roles for r in settings.kb_admin_roles)


def require_kb_admin(user: User = Depends(get_user)) -> User:
    if not is_kb_admin(user):
        raise HTTPException(403, f"Cần quyền {settings.kb_admin_permission}")
    return user
