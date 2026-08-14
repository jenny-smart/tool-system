from __future__ import annotations

from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from tools.common.config_loader import get_service_account_info, read_sheet_records


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SUPPORTED_AREAS = ("台北", "台中", "桃園", "新竹", "高雄")


@lru_cache(maxsize=1)
def _client() -> gspread.Client:
    credentials = Credentials.from_service_account_info(
        get_service_account_info(), scopes=SCOPES
    )
    return gspread.authorize(credentials)


def get_sheet_config(area: str) -> tuple[str, Optional[int]]:
    if area not in SUPPORTED_AREAS:
        raise ValueError(f"不支援的地區：{area}")
    for record in read_sheet_records("清潔異動設定"):
        if str(record.get("地區", "")).strip() != area:
            continue
        if str(record.get("啟用", "")).strip().lower() in ("否", "false", "0", "停用"):
            continue
        spreadsheet_id = str(record.get("試算表ID", "")).strip()
        gid_text = str(record.get("GID", "")).strip()
        if not spreadsheet_id:
            break
        try:
            return spreadsheet_id, int(gid_text) if gid_text else None
        except ValueError as exc:
            raise RuntimeError(f"{area} 的 GID 必須是數字") from exc
    raise RuntimeError(f"共用設定表找不到「{area}」清潔異動設定")


def get_worksheet(area: str, tab_name: str = "清潔異動"):
    spreadsheet_id, gid = get_sheet_config(area)
    try:
        spreadsheet = _client().open_by_key(spreadsheet_id)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise RuntimeError(f"服務帳號無法開啟「{area}」清潔異動試算表") from exc
    if gid is not None:
        for worksheet in spreadsheet.worksheets():
            if worksheet.id == gid:
                return worksheet
        raise RuntimeError(f"{area} 清潔異動試算表找不到 GID {gid}")
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound as exc:
        raise RuntimeError(f"{area} 試算表找不到「{tab_name}」分頁") from exc
