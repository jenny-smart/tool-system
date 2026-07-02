from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import streamlit as st
except Exception:
    st = None


EI_BASE_URL = "https://www.ei.com.tw/InvoiceRent"
LOGIN_ENDPOINT = "/enterlogin.action"
ADD_INVOICE_ENDPOINT = "/addInvoice.action"
INVOICE_LIST_ENDPOINT = "/common/invoice/invoicelist.jsp"


AREA_ENV = {
    "taipei": {
        "label": "台北",
        "userid": "TAIPEI_EI_USERID",
        "password": "TAIPEI_EI_PASSWORD",
    },
    "taichung": {
        "label": "台中",
        "userid": "TAICHUNG_EI_USERID",
        "password": "TAICHUNG_EI_PASSWORD",
    },
    "taoyuan": {
        "label": "桃園",
        "userid": "TAOYUAN_EI_USERID",
        "password": "TAOYUAN_EI_PASSWORD",
    },
    "hsinchu": {
        "label": "新竹",
        "userid": "HSINCHU_EI_USERID",
        "password": "HSINCHU_EI_PASSWORD",
    },
    "kaohsiung": {
        "label": "高雄",
        "userid": "KAOHSIUNG_EI_USERID",
        "password": "KAOHSIUNG_EI_PASSWORD",
    },
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


@dataclass(frozen=True)
class EICredentials:
    area: str
    label: str
    userid: str
    password: str

    @property
    def masked_userid(self) -> str:
        if len(self.userid) <= 4:
            return "*" * len(self.userid)
        return f"{self.userid[:2]}***{self.userid[-2:]}"


def _streamlit_secret(name: str) -> str:
    if st is None:
        return ""
    try:
        value = st.secrets.get(name, "")
        return str(value).strip()
    except Exception:
        return ""


def get_secret(name: str) -> str:
    return os.getenv(name, "").strip() or _streamlit_secret(name)


def normalize_area(area: str) -> str:
    key = str(area or "").strip()
    normalized = AREA_ALIASES.get(key) or AREA_ALIASES.get(key.lower())
    if not normalized:
        supported = ", ".join(item["label"] for item in AREA_ENV.values())
        raise ValueError(f"不支援的 EI 地區：{area}，目前支援：{supported}")
    return normalized


def get_area_label(area: str) -> str:
    return AREA_ENV[normalize_area(area)]["label"]


def get_area_credentials(area: str, required: bool = True) -> EICredentials | None:
    area_key = normalize_area(area)
    meta = AREA_ENV[area_key]
    userid = get_secret(meta["userid"])
    password = get_secret(meta["password"])

    if userid and password:
        return EICredentials(
            area=area_key,
            label=meta["label"],
            userid=userid,
            password=password,
        )

    if required:
        raise RuntimeError(
            f"{meta['label']} EI 帳密尚未設定，請設定 "
            f"{meta['userid']} / {meta['password']}"
        )
    return None


def get_area_status() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for area_key, meta in AREA_ENV.items():
        userid = get_secret(meta["userid"])
        password = get_secret(meta["password"])
        rows.append(
            {
                "area": area_key,
                "label": meta["label"],
                "userid_env": meta["userid"],
                "password_env": meta["password"],
                "has_userid": bool(userid),
                "has_password": bool(password),
                "configured": bool(userid and password),
            }
        )
    return rows


def get_area_options() -> list[tuple[str, str]]:
    return [(key, meta["label"]) for key, meta in AREA_ENV.items()]
