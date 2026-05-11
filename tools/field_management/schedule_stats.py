from __future__ import annotations

import argparse, io, json, os, re, sys, traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def add_month_same_day_yyyymmdd(date_key: str, months: int = 1) -> str:
    year = int(date_key[:4])
    month = int(date_key[4:6]) + months
    day = date_key[6:8]
    while month > 12:
        year += 1
        month -= 12
    while month < 1:
        year -= 1
        month += 12
    return f"{year}{month:02d}{day}"


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
    return Credentials.from_service_account_info(get_service_account_info(), scopes=SCOPES)


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


def load_system_config(system_name: str = "外場日排程系統") -> dict[str, Any]:
    config_path = Path("config/systems.yaml")
    if not config_path.exists():
        raise RuntimeError("找不到 config/systems.yaml")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for system in data.get("systems", []):
        if system.get("name") == system_name:
            return system

    raise RuntimeError(f"systems.yaml 找不到系統：{system_name}")


def get_folder_id(cfg: dict[str, Any], folder_type: str, area: str | None = None) -> str:
    value = cfg.get("folder_ids", {}).get(folder_type)
    if isinstance(value, dict):
        value = value.get(area or "")
    if not value:
        raise RuntimeError(f"尚未設定資料夾 ID：{folder_type} / {area}")
    return str(value).strip()


def get_spreadsheet_id(cfg: dict[str, Any], sheet_type: str, area: str) -> str:
    value = cfg.get("spreadsheet_ids", {}).get(sheet_type, {}).get(area)
    if not value:
        raise RuntimeError(f"尚未設定試算表 ID：{sheet_type} / {area}")
    return str(value).strip()


def area_list_from_config(cfg: dict[str, Any]) -> list[str]:
    if cfg.get("areas"):
        return list(cfg["areas"])
    for key in ["roster", "salary", "office"]:
        m = cfg.get("spreadsheet_ids", {}).get(key)
        if isinstance(m, dict):
            return list(m.keys())
    return ["台北", "台中"]


def list_files_in_folder(drive, folder_id: str) -> list[dict[str, Any]]:
    files, token = [], None
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


def find_file_by_possible_names(drive, folder_id: str, possible_names: list[str]) -> dict[str, Any]:
    targets = [normalize_file_name(n) for n in possible_names]
    candidates = []
    for f in list_files_in_folder(drive, folder_id):
        name = f.get("name", "")
        candidates.append(name)
        if normalize_file_name(name) in targets:
            return f
    raise RuntimeError("找不到來源檔案：" + " / ".join(possible_names) + "；資料夾內目前檔案：" + "、".join(candidates[:80]))


def read_google_sheet_values(sheets, spreadsheet_id: str, range_name: str = "A:ZZ") -> list[list[Any]]:
    res = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


def download_file_bytes(drive, file_id: str, mime_type: str) -> bytes:
    if mime_type == GOOGLE_SHEETS_MIME:
        req = drive.files().export_media(
            fileId=file_id,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        req = drive.files().get_media(fileId=file_id)
    return req.execute()


def read_file_values(drive, sheets, file: dict[str, Any]) -> list[list[Any]]:
    if file.get("mimeType") == GOOGLE_SHEETS_MIME:
        return read_google_sheet_values(sheets, file["id"])

    content = download_file_bytes(drive, file["id"], file.get("mimeType", ""))
    name = file.get("name", "").lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), header=None)
    else:
        from tools.field_management.google_sheet_reader import read_drive_spreadsheet_values


def read_file_values(drive, sheets, file):
    return read_drive_spreadsheet_values(drive, sheets, file)


def ensure_rectangular(values: list[list[Any]], cols: int | None = None) -> list[list[Any]]:
    if not values:
        return []
    max_cols = cols or max(len(r) for r in values)
    out = []
    for row in values:
        new_row = list(row[:max_cols])
        while len(new_row) < max_cols:
            new_row.append("")
        out.append(new_row)
    return out


def is_blank_row(row: list[Any]) -> bool:
    return all(str(v).strip() == "" for v in row)


def clear_range(sheets, spreadsheet_id: str, range_name: str) -> None:
    sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=range_name, body={}).execute()


def write_values(sheets, spreadsheet_id: str, range_name: str, values: list[list[Any]]) -> None:
    if not values:
        return
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def clear_and_write_values(sheets, spreadsheet_id: str, range_name: str, values: list[list[Any]]) -> None:
    clear_range(sheets, spreadsheet_id, range_name)
    write_values(sheets, spreadsheet_id, range_name, values)

PASTE_MAP = {1:"R4",2:"AD4",3:"AP4",4:"BB4",5:"BN4",6:"BZ4",7:"CL4",8:"CX4",9:"DJ4",10:"DV4",11:"EH4",12:"ET4"}
TARGET_SHEET_NAME = "2026排班統計表"


def filter_schedule_values(values: list[list[Any]]) -> list[list[Any]]:
    out = []
    for row in values:
        if is_blank_row(row):
            continue
        col_b = str(row[1]).strip() if len(row) > 1 else ""
        if "檸檬人" in col_b:
            continue
        out.append(row)
    return out


def run_schedule_stats_for_area(cfg: dict[str, Any], area: str, date_key: str) -> None:
    drive, sheets = get_drive_service(), get_sheets_service()
    source_folder_id = get_folder_id(cfg, "schedule_stats", area)
    target_spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    for key in [date_key, add_month_same_day_yyyymmdd(date_key, 1)]:
        paste_cell = PASTE_MAP[int(key[4:6])]
        file_base = f"排班統計表{key}-{area}"
        log(f"開始處理排班統計表：{file_base}")

        file = find_file_by_possible_names(drive, source_folder_id, [file_base, f"{file_base}.xlsx", f"{file_base}.xls"])
        values = ensure_rectangular(filter_schedule_values(read_file_values(drive, sheets, file)))
        if not values:
            raise RuntimeError(f"排班統計表沒有可貼入資料：{file_base}")

        target_range = f"{TARGET_SHEET_NAME}!{paste_cell}"
        clear_and_write_values(sheets, target_spreadsheet_id, target_range, values)
        log(f"完成：{file.get('name')} → {target_range} / rows={len(values)}")


def main(date_key: str | None = None, area: str | None = None, system_name: str = "外場日排程系統") -> None:
    cfg = load_system_config(system_name)
    date_key = date_key or today_yyyymmdd()
    for a in ([area] if area else area_list_from_config(cfg)):
        run_schedule_stats_for_area(cfg, a, date_key)
    log("schedule_stats.py 全部完成")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=today_yyyymmdd())
    p.add_argument("--area", default="")
    p.add_argument("--system-name", default="外場日排程系統")
    args = p.parse_args()
    main(args.date, args.area or None, args.system_name)
