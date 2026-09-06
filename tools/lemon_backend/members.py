from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from .client import BackendClient


STORED_VALUE_EXPORT_PATH = "/member/export_stored_value"
STORED_VALUE_EXPORT_COLUMNS = {
    "mv_id",
    "客戶姓名",
    "email",
    "電話",
    "地址",
    "LINE@",
    "剩餘儲值金",
    "會員等級",
}


def _clean_cell(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def normalize_member_phone(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = "".join(ch for ch in _clean_cell(value) if ch.isdigit())
    if len(digits) == 9 and digits.startswith("9"):
        digits = "0" + digits
    return digits


def members_from_dataframe(df: pd.DataFrame, area: str) -> list[dict[str, Any]]:
    missing = sorted(STORED_VALUE_EXPORT_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"儲值金匯出檔缺少欄位：{'、'.join(missing)}")

    members: dict[str, dict[str, Any]] = {}
    for raw in df.to_dict(orient="records"):
        phone = normalize_member_phone(raw.get("電話"))
        member_id = _clean_cell(raw.get("mv_id"))
        name = _clean_cell(raw.get("客戶姓名"))
        email = _clean_cell(raw.get("email"))
        key = member_id or phone or email.lower() or name
        if not key:
            continue
        current = members.setdefault(
            key,
            {
                "area": area,
                "member_id": member_id,
                "name": name,
                "email": email,
                "phone": phone,
                "address": _clean_cell(raw.get("地址")),
                "line_url": _clean_cell(raw.get("LINE@")),
                "member_level": _clean_cell(raw.get("會員等級")),
                "stored_value": 0.0,
            },
        )
        for field, source in (
            ("name", "客戶姓名"),
            ("email", "email"),
            ("address", "地址"),
            ("line_url", "LINE@"),
            ("member_level", "會員等級"),
        ):
            value = _clean_cell(raw.get(source))
            if value and not current[field]:
                current[field] = value
        try:
            current["stored_value"] = max(
                float(current["stored_value"] or 0),
                float(raw.get("剩餘儲值金") or 0),
            )
        except (TypeError, ValueError):
            pass
    return list(members.values())


def export_stored_value_members(
    area: str,
    client: BackendClient | None = None,
) -> list[dict[str, Any]]:
    backend = client or BackendClient(area)
    backend.ensure_login()
    response = backend.session.get(STORED_VALUE_EXPORT_PATH)
    response.raise_for_status()
    if backend.session.looks_like_login_page(response):
        raise RuntimeError(f"{area}後台儲值金名單匯出失敗：登入已失效")
    try:
        frame = pd.read_excel(BytesIO(response.content), engine="calamine")
    except Exception as exc:
        raise RuntimeError(f"{area}後台儲值金匯出檔無法讀取：{exc}") from exc
    return members_from_dataframe(frame, area)
