from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from tools.bank_statement.capture import CapturedTable
from tools.bank_statement.google_config import load_google_credentials, load_sheet_target
from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service


SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)
FUBON_AREAS = ("台北", "台中", "桃園", "新竹", "高雄")


def _note_first_line(value: str) -> str:
    for line in value.replace("\r", "").splitlines():
        line = line.replace("更多", "").strip()
        if line:
            return line
    return ""


def _normalize_amount(value: str) -> str:
    text = value.replace(",", "").replace("NT$", "").strip()
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    return format(number.normalize(), "f")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_target_row(row: list[str]) -> list[str]:
    """正規化報表 B:H：日期、時間、摘要、支出、存入、餘額、附註。"""
    padded = [str(value) for value in row[:7]] + [""] * max(0, 7 - len(row))
    return [
        _normalize_text(padded[0]),
        _normalize_text(padded[1]),
        _normalize_text(padded[2]),
        _normalize_amount(padded[3]),
        _normalize_amount(padded[4]),
        _normalize_amount(padded[5]),
        _note_first_line(padded[6]),
    ]


def transaction_time_key(row: list[str]) -> str:
    """依使用者指定，只以報表 C 欄的完整交易時間判斷是否已同步。"""
    if len(row) <= 1:
        return ""
    value = _normalize_text(str(row[1]))
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return value


def table_target_rows(table: CapturedTable) -> list[list[str]]:
    if len(table.headers) != 7:
        raise ValueError(f"富邦明細預期 7 欄，實際為 {len(table.headers)} 欄：{table.headers}")
    rows: list[list[str]] = []
    for row in table.rows:
        if len(row) != 7:
            continue
        output = [str(value).strip() for value in row]
        output[6] = output[6].replace("\r", "").replace("更多", "").strip()
        rows.append(output)
    return rows


def filter_existing_rows(table: CapturedTable, existing_rows: list[list[str]]) -> CapturedTable:
    existing_keys = {transaction_time_key(row) for row in existing_rows if transaction_time_key(row)}
    new_rows = [
        row
        for row in table_target_rows(table)
        if transaction_time_key(row) and transaction_time_key(row) not in existing_keys
    ]
    return CapturedTable(headers=table.headers, rows=new_rows)


def read_and_filter(table: CapturedTable, area: str, bank: str) -> CapturedTable:
    target = load_sheet_target(area, bank)
    credentials = Credentials.from_service_account_info(load_google_credentials(), scopes=SCOPES)
    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(target.spreadsheet_id).get_worksheet_by_id(target.worksheet_gid)
    existing_rows = worksheet.get("B:H")
    return filter_existing_rows(table, existing_rows)


def sync_fubon_master_sheet(
    table: CapturedTable,
    area: str,
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    service = service or get_sheets_service()
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    titles = {sheet["properties"]["title"] for sheet in meta.get("sheets", [])}
    missing = [f"富邦銀行-{name}" for name in FUBON_AREAS if f"富邦銀行-{name}" not in titles]
    if missing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [
                {"addSheet": {"properties": {"title": title, "gridProperties": {"frozenRowCount": 1}}}}
                for title in missing
            ]},
        ).execute()
    for name in FUBON_AREAS:
        title = f"富邦銀行-{name}"
        current = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A1:G1",
        ).execute().get("values", [])
        if not current:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{title}'!A1:G1",
                valueInputOption="RAW",
                body={"values": [table.headers]},
            ).execute()
    title = f"富邦銀行-{area}"
    # 此分頁是「目前未登記清單」而非歷史資料，每次以最新比對結果重建。
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A2:G",
        body={},
    ).execute()
    if table.rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A2:G{len(table.rows) + 1}",
            # 保留銀行原始日期文字，避免 A/B 欄被轉成 2026/8/6 與 46240.36983。
            valueInputOption="RAW",
            body={"values": table.rows},
        ).execute()
    return len(table.rows)
