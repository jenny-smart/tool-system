from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from google_sheet_reader import read_drive_spreadsheet_values
from logger import log


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def add_month_same_day_yyyymmdd(date_key: str, months: int = 1) -> str:
    year = int(date_key[:4])
    month = int(date_key[4:6]) + months
    day = int(date_key[6:8])

    while month > 12:
        year += 1
        month -= 12

    while month < 1:
        year -= 1
        month += 12

    return f"{year}{month:02d}{day:02d}"


def normalize_file_name(name: str) -> str:
    name = str(name or "")
    name = re.sub(r"\.(xlsx|xls|csv)$", "", name, flags=re.I)
    name = name.replace("－", "-").replace("–", "-").replace("—", "-")
    name = re.sub(r"\s+", "", name)
    return name.strip()


def get_service_account_info() -> dict[str, Any]:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        return json.loads(raw_json)

    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))

    try:
        import streamlit as st

        for key in ["GOOGLE_SERVICE_ACCOUNT", "gcp_service_account"]:
            try:
                info = dict(st.secrets[key])
                if info:
                    return info
            except Exception:
                pass
    except Exception:
        pass

    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")


def get_credentials() -> Credentials:
    return Credentials.from_service_account_info(
        get_service_account_info(),
        scopes=SCOPES,
    )


def get_drive_service():
    return build(
        "drive",
        "v3",
        credentials=get_credentials(),
        cache_discovery=False,
    )


def get_sheets_service():
    return build(
        "sheets",
        "v4",
        credentials=get_credentials(),
        cache_discovery=False,
    )


def load_system_config(system_name: str = "外場日排程系統") -> dict[str, Any]:
    config_path = Path("config/systems.yaml")

    if not config_path.exists():
        raise RuntimeError("找不到 config/systems.yaml")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    for system in data.get("systems", []):
        if system.get("name") == system_name:
            return system

    raise RuntimeError(f"systems.yaml 找不到系統：{system_name}")


def get_folder_id(
    cfg: dict[str, Any],
    folder_type: str,
    area: str | None = None,
) -> str:
    value = cfg.get("folder_ids", {}).get(folder_type)

    if isinstance(value, dict):
        value = value.get(area or "")

    if not value:
        raise RuntimeError(f"尚未設定資料夾 ID：{folder_type} / {area}")

    return str(value).strip()


def get_spreadsheet_id(
    cfg: dict[str, Any],
    sheet_type: str,
    area: str,
) -> str:
    value = cfg.get("spreadsheet_ids", {}).get(sheet_type, {}).get(area)

    if not value:
        raise RuntimeError(f"尚未設定試算表 ID：{sheet_type} / {area}")

    return str(value).strip()


def area_list_from_config(cfg: dict[str, Any]) -> list[str]:
    if cfg.get("areas"):
        return list(cfg["areas"])

    for key in ["roster", "salary", "office"]:
        mapping = cfg.get("spreadsheet_ids", {}).get(key)
        if isinstance(mapping, dict):
            return list(mapping.keys())

    return ["台北", "台中"]


def list_files_in_folder(drive, folder_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    token = None

    while True:
        res = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        files.extend(res.get("files", []))
        token = res.get("nextPageToken")

        if not token:
            return files


def find_file_by_possible_names(
    drive,
    folder_id: str,
    possible_names: list[str],
) -> dict[str, Any]:
    targets = [normalize_file_name(name) for name in possible_names]
    candidates: list[str] = []

    for file in list_files_in_folder(drive, folder_id):
        name = file.get("name", "")
        candidates.append(name)

        if normalize_file_name(name) in targets:
            return file

    raise RuntimeError(
        "找不到來源檔案："
        + " / ".join(possible_names)
        + "；資料夾內目前檔案："
        + "、".join(candidates[:80])
    )


def read_file_values(drive, sheets, file: dict[str, Any]) -> list[list[Any]]:
    return read_drive_spreadsheet_values(drive, sheets, file)


def ensure_rectangular(
    values: list[list[Any]],
    cols: int | None = None,
) -> list[list[Any]]:
    if not values:
        return []

    max_cols = cols or max(len(row) for row in values)
    output: list[list[Any]] = []

    for row in values:
        new_row = list(row[:max_cols])
        while len(new_row) < max_cols:
            new_row.append("")
        output.append(new_row)

    return output


def clear_range(sheets, spreadsheet_id: str, range_name: str) -> None:
    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        body={},
    ).execute()


def write_values(
    sheets,
    spreadsheet_id: str,
    range_name: str,
    values: list[list[Any]],
) -> None:
    if not values:
        return

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def clear_and_write_values(
    sheets,
    spreadsheet_id: str,
    range_name: str,
    values: list[list[Any]],
) -> None:
    clear_range(sheets, spreadsheet_id, range_name)
    write_values(sheets, spreadsheet_id, range_name, values)


def sheet_names_for_date(date_key: str) -> tuple[str, str]:
    date = datetime.strptime(date_key, "%Y%m%d")

    return (
        f"專員班表查詢本月_{date.strftime('%m%d')}",
        f"專員班表查詢次月_{date.strftime('%m%d')}",
    )


def run_staff_schedule_for_area(
    cfg: dict[str, Any],
    area: str,
    date_key: str,
) -> None:
    drive = get_drive_service()
    sheets = get_sheets_service()

    source_folder_id = get_folder_id(cfg, "staff_schedule", area)
    target_spreadsheet_id = get_spreadsheet_id(cfg, "office", area)

    current_sheet, next_sheet = sheet_names_for_date(date_key)

    jobs = [
        (date_key, current_sheet),
        (add_month_same_day_yyyymmdd(date_key, 1), next_sheet),
    ]

    for file_date_key, target_sheet in jobs:
        file_base = f"{file_date_key}專員班表-{area}"

        log(f"開始處理專員班表：{file_base}")

        file = find_file_by_possible_names(
            drive,
            source_folder_id,
            [
                file_base,
                f"{file_base}.xlsx",
                f"{file_base}.xls",
                f"{file_base}.csv",
            ],
        )

        raw_values = read_file_values(drive, sheets, file)

        values = ensure_rectangular(
            [list(row[:7]) for row in raw_values],
            7,
        )

        if not values:
            raise RuntimeError(f"專員班表沒有資料：{file_base}")

        target_range = f"{target_sheet}!A:G"

        clear_and_write_values(
            sheets,
            target_spreadsheet_id,
            target_range,
            values,
        )

        log(
            f"完成：{file.get('name')} → {target_range} / rows={len(values)}"
        )


def main(
    date_key: str | None = None,
    area: str | None = None,
    system_name: str = "外場日排程系統",
) -> None:
    cfg = load_system_config(system_name)
    date_key = date_key or today_yyyymmdd()

    areas = [area] if area else area_list_from_config(cfg)

    for current_area in areas:
        run_staff_schedule_for_area(cfg, current_area, date_key)

    log("staff_schedule.py 全部完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_yyyymmdd())
    parser.add_argument("--area", default="")
    parser.add_argument("--system-name", default="外場日排程系統")

    args = parser.parse_args()

    main(
        args.date,
        args.area or None,
        args.system_name,
    )
