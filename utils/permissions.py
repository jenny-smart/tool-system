from __future__ import annotations
from pathlib import Path
from typing import Any
import streamlit as st
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
ROLES_PATH = BASE_DIR / "config" / "roles.yaml"

LOG_GROUPS = {
    "daily_scheduler": [
        "schedule_report",
        "staff_schedule",
        "orders_report",
        "staff_info",
        "send_daily_result",
    ],
    "field_daily_schedule": [
        "field_management",
        "schedule_stats",
        "orders",
        "staff_profile",
    ],
    "performance_report": [
        "performance_report",
    ],
    # ★ 新增：客服排程系統 log 群組
    "service_schedule": [
        "service_schedule_stats",
        "service_daily_report",
        "service_revenue",
    ],
}

def load_roles() -> dict[str, Any]:
    if not ROLES_PATH.exists():
        return {}
    data = yaml.safe_load(ROLES_PATH.read_text(encoding="utf-8")) or {}
    return data.get("roles", {}) or {}

def get_role_config() -> dict[str, Any]:
    role = str(st.session_state.get("role", ""))
    roles = load_roles()
    return roles.get(role, {}) or {}

def can_access_system(system_type: str) -> bool:
    cfg = get_role_config()
    allowed = cfg.get("systems", [])
    # systems: "*" 代表允許所有系統（admin 用）
    if allowed == "*" or allowed == ["*"]:
        return True
    return str(system_type) in allowed

def can_access_page(page_name: str) -> bool:
    return str(page_name) in get_role_config().get("pages", [])

def can_access_log(log_group: str) -> bool:
    return str(log_group) in get_role_config().get("logs", [])

def get_allowed_log_jobs() -> list[str]:
    allowed_groups = get_role_config().get("logs", []) or []
    jobs: list[str] = []
    for group in allowed_groups:
        jobs.extend(LOG_GROUPS.get(group, []))
    return jobs
