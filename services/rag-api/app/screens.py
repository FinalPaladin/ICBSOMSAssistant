"""Screen Registry: nhận biết màn hình OMS hiện tại. Chỉ là gợi ý, KHÔNG mở rộng quyền."""
import re
from dataclasses import dataclass, field

import yaml

from .auth import User
from .config import settings


@dataclass
class Screen:
    screen_id: str
    name: str
    module: str = ""
    required_permission: str = ""
    allowed_state_fields: list[str] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)


_screens: dict[str, Screen] = {}


def load() -> None:
    _screens.clear()
    try:
        with open(settings.screens_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return
    for s in data.get("screens", []):
        sc = Screen(
            screen_id=s["screen_id"], name=s.get("name", s["screen_id"]), module=s.get("module", ""),
            required_permission=s.get("required_permission", ""),
            allowed_state_fields=s.get("allowed_state_fields", []) or [],
            suggested_questions=s.get("suggested_questions", []) or [])
        _screens[sc.screen_id] = sc


def all_screens() -> list[Screen]:
    return list(_screens.values())


def accessible(user: User) -> list[Screen]:
    return [s for s in _screens.values() if not s.required_permission or s.required_permission in user.permissions]


def resolve(user: User, screen_id: str | None) -> Screen | None:
    if not screen_id:
        return None
    sc = _screens.get(screen_id)
    if sc is None:
        return None
    if sc.required_permission and sc.required_permission not in user.permissions:
        return None
    return sc


def clean_state(sc: Screen, state: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in sc.allowed_state_fields:
        if state and k in state and state[k] is not None:
            v = re.sub(r"\s+", " ", str(state[k])).strip()[:60]
            if v:
                out[k] = v
    return out


DEICTIC = re.compile(
    r"(màn hình|trang|đơn|nút|chức năng|tab|form|popup)\s+(này|nầy|hiện tại|đang mở)|ở đây|bước (này|tiếp theo)",
    re.IGNORECASE)


def is_deictic(question: str) -> bool:
    return bool(DEICTIC.search(question))
