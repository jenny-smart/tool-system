from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import streamlit as st
except Exception:
    st = None

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_service_account_info() -> dict[str, Any]:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT", "").strip()
    if raw:
        return json.loads(raw)

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        return json.loads(raw_json)

    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))

    if st is not None:
        for key in ["GOOGLE_SERVICE_ACCOUNT", "gcp_service_account"]:
            try:
                info = dict(st.secrets[key])
                if info:
                    return info
            except Exception:
                pass

    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")


def get_sheets_service():
    creds = service_account.Credentials.from_service_account_info(
        get_service_account_info(),
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_master_spreadsheet_id() -> str:
    spreadsheet_id = (
        os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip()
        or os.getenv("MASTER_LOG_SPREADSHEET_ID", "").strip()
        or os.getenv("LOG_SPREADSHEET_ID", "").strip()
        or "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
    )


def read_sheet(sheet_name: str, spreadsheet_id: str = "") -> list[list[str]]:
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    service = get_sheets_service()

    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:Z",
    ).execute()

    return res.get("values", [])


def read_sheet_records(sheet_name: str, spreadsheet_id: str = "") -> list[dict[str, str]]:
    rows = read_sheet(sheet_name, spreadsheet_id)

    if not rows:
        return []

    headers = [str(h).strip() for h in rows[0]]
    records: list[dict[str, str]] = []

    for row in rows[1:]:
        record: dict[str, str] = {}
        for i, header in enumerate(headers):
            record[header] = str(row[i]).strip() if i < len(row) else ""
        records.append(record)

    return records


def is_enabled(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in ["true", "1", "yes", "y", "啟用", "✅", "✅ 啟用"]


def get_system_config(system_name: str) -> dict[str, str]:
    records = read_sheet_records("系統設定")

    for record in records:
        name = (
            record.get("系統名稱", "")
            or record.get("name", "")
        ).strip()

        if name == system_name:
            return record

    raise RuntimeError(f"主控檔找不到系統設定：{system_name}")


def load_monthly_config() -> dict[str, Any]:
    system = get_system_config("月排程系統")

    root_folder_id = (
        system.get("月排程總根目錄ID", "").strip()
        or system.get("月排程根目錄 ID", "").strip()
        or system.get("月排程根目錄ID", "").strip()
        or system.get("共用雲端資料夾ID / 根目錄ID", "").strip()
        or system.get("folder_id", "").strip()
    )

    if not root_folder_id:
        raise RuntimeError("月排程系統缺少月排程根目錄 ID")

    area_records = read_sheet_records("月排程地區設定")
    areas: dict[str, str] = {}

    for record in area_records:
        area = record.get("地區", "").strip()
        folder_id = record.get("地區根目錄ID", "").strip()
        enabled = is_enabled(record.get("啟用", ""))

        if area and folder_id and enabled:
            areas[area] = folder_id

    return {
        "root_folder_id": root_folder_id,
        "areas": areas,
        "raw_system": system,
    }
