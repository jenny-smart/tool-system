from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
USERS_PATH = BASE_DIR / "config" / "users.yaml"


def load_users() -> dict[str, Any]:
    if not USERS_PATH.exists():
        return {}

    data = yaml.safe_load(USERS_PATH.read_text(encoding="utf-8")) or {}
    return data.get("users", {}) or {}


def authenticate(username: str, password: str) -> dict[str, str] | None:
    username = str(username or "").strip()
    password = str(password or "")

    users = load_users()

    if username not in users:
        return None

    user = users.get(username, {})

    if str(user.get("password", "")) != password:
        return None

    return {
        "username": username,
        "role": str(user.get("role", "")),
    }
