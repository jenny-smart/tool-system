from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import streamlit as st
except Exception:
    st = None


BASE_URL_PROD = "https://backend.lemonclean.com.tw"
BASE_URL_DEV = "https://backend-dev.lemonclean.com.tw"

AREA_ENV = {
    "taipei": ("台北", "TAIPEI_EMAIL", "TAIPEI_PASSWORD"),
    "taichung": ("台中", "TAICHUNG_EMAIL", "TAICHUNG_PASSWORD"),
    "taoyuan": ("桃園", "TAOYUAN_EMAIL", "TAOYUAN_PASSWORD"),
    "hsinchu": ("新竹", "HSINCHU_EMAIL", "HSINCHU_PASSWORD"),
    "kaohsiung": ("高雄", "KAOHSIUNG_EMAIL", "KAOHSIUNG_PASSWORD"),
}

AREA_ALIASES = {
    "taipei": "taipei",
    "台北": "taipei",
    "taichung": "taichung",
    "台中": "taichung",
    "taoyuan": "taoyuan",
    "桃園": "taoyuan",
    "hsinchu": "hsinchu",
    "新竹": "hsinchu",
    "kaohsiung": "kaohsiung",
    "高雄": "kaohsiung",
}

PURCHASE_FILTER_PARAMS = {
    "keyword": "",
    "name": "",
    "phone": "",
    "orderNo": "",
    "date_s": "",
    "date_e": "",
    "clean_date_s": "",
    "clean_date_e": "",
    "paid_at_s": "",
    "paid_at_e": "",
    "refundDateS": "",
    "refundDateE": "",
    "buy": "",
    "area_id": "",
    "isCharge": "",
    "isRefund": "",
    "payway": "",
    "purchase_status": "",
    "progress_status": "",
    "invoiceStatus": "",
    "otherFee": "",
    "orderBy": "",
}

BOOKING_ENDPOINTS_BY_PAYWAY = {
    "信用卡": "/booking/single",
    "ATM": "/booking/single",
    "儲值金": "/booking/stored_value_routine",
}


def get_booking_endpoint(payway: str) -> str:
    return BOOKING_ENDPOINTS_BY_PAYWAY.get(str(payway or "").strip(), "/booking/single")


@dataclass(frozen=True)
class BackendCredentials:
    area: str
    label: str
    email: str
    password: str

    @property
    def masked_email(self) -> str:
        if "@" not in self.email:
            return "***"
        head, domain = self.email.split("@", 1)
        return f"{head[:2]}***@{domain}"


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _streamlit_secret(name: str) -> str:
    if st is None:
        return ""
    try:
        return _secret_value(st.secrets.get(name, ""))
    except Exception:
        return ""


def _mapping_get(mapping: Any, key: str) -> Any:
    if mapping is None:
        return None
    try:
        if hasattr(mapping, "get"):
            return mapping.get(key)
        return mapping[key]
    except Exception:
        return None


def _streamlit_account_secret(area: str, field: str) -> str:
    """Read existing Streamlit secrets: accounts.<area>.email/password."""
    if st is None:
        return ""
    area_key = normalize_area(area)
    label = AREA_ENV[area_key][0]
    candidate_area_keys = [area_key, label]

    try:
        accounts = _mapping_get(st.secrets, "accounts")
        for candidate in candidate_area_keys:
            area_config = _mapping_get(accounts, candidate)
            value = _mapping_get(area_config, field)
            if _secret_value(value):
                return _secret_value(value)
    except Exception:
        return ""
    return ""


def get_secret(name: str) -> str:
    return os.getenv(name, "").strip() or _streamlit_secret(name)


def normalize_area(area: str) -> str:
    key = str(area or "").strip()
    normalized = AREA_ALIASES.get(key) or AREA_ALIASES.get(key.lower())
    if not normalized:
        labels = ", ".join(value[0] for value in AREA_ENV.values())
        raise ValueError(f"不支援的後台地區：{area}，目前支援：{labels}")
    return normalized


def get_account_secret(area: str, field: str, fallback_env: str) -> str:
    """Read Lemon Backend credentials from accounts.<area> first, then flat env/secrets."""
    return _streamlit_account_secret(area, field) or get_secret(fallback_env)


def get_credentials(area: str, required: bool = True) -> BackendCredentials | None:
    key = normalize_area(area)
    label, email_env, password_env = AREA_ENV[key]
    email = get_account_secret(key, "email", email_env)
    password = get_account_secret(key, "password", password_env)
    if email and password:
        return BackendCredentials(key, label, email, password)
    if required:
        raise RuntimeError(
            f"{label} 後台帳密未設定：accounts.{key}.email/password 或 {email_env} / {password_env}"
        )
    return None


def get_base_url(env_name: str | None = None) -> str:
    override = get_secret("LEMON_BACKEND_BASE_URL")
    if override:
        return override.rstrip("/")
    env_value = (env_name or get_secret("LEMON_BACKEND_ENV") or "prod").lower()
    return BASE_URL_DEV if env_value == "dev" else BASE_URL_PROD


def get_area_status() -> list[dict[str, Any]]:
    rows = []
    for key, (label, email_env, password_env) in AREA_ENV.items():
        has_email = bool(get_account_secret(key, "email", email_env))
        has_password = bool(get_account_secret(key, "password", password_env))
        rows.append(
            {
                "area": key,
                "label": label,
                "email_env": email_env,
                "password_env": password_env,
                "account_secret_path": f"accounts.{key}.email/password",
                "has_email": has_email,
                "has_password": has_password,
                "configured": has_email and has_password,
            }
        )
    return rows
