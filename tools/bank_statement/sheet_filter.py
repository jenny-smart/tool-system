from __future__ import annotations

import re
from collections import Counter
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
BANK_AREAS = ("台北", "台中", "桃園", "新竹", "高雄")


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


def _integer_amount(value: str) -> str:
    normalized = _normalize_amount(value)
    if not normalized:
        return ""
    try:
        return f"{Decimal(normalized):,.0f}"
    except InvalidOperation:
        return value


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


def transaction_identity_key(row: list[str]) -> tuple[str, ...]:
    normalized = normalize_target_row(row)
    return (
        transaction_time_key(normalized),
        normalized[2],
        normalized[3],
        normalized[4],
        normalized[5],
    )


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


def filter_existing_rows(
    table: CapturedTable,
    existing_rows: list[list[str]],
    *,
    strict: bool = False,
) -> CapturedTable:
    key_fn = transaction_identity_key if strict else transaction_time_key
    existing_keys = {key_fn(row) for row in existing_rows if transaction_time_key(row)}
    new_rows = [
        row
        for row in table_target_rows(table)
        if transaction_time_key(row) and key_fn(row) not in existing_keys
    ]
    return CapturedTable(headers=table.headers, rows=new_rows)


def _accounting_date_key(value: str) -> str:
    text = _normalize_text(str(value))
    # 財報 B 欄是交易日期時間；元大清單 A 欄是純帳務日期，只比較日期部分。
    date_part = text.split(" ", 1)[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%y/%m/%d", "%y-%m-%d"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_part


def _report_transaction_time_key(value: str) -> str:
    text = _normalize_text(str(value))
    for fmt in (
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%y/%m/%d %H:%M:%S",
        "%y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return ""


def filter_yuanta_report_rows(
    table: CapturedTable,
    report_accounting_dates: list[list[str]],
) -> CapturedTable:
    """以財報 B 欄日期對應元大清單 A 欄，按同日既有列數扣除。"""
    report_values = [str(row[0]) for row in report_accounting_dates if row]
    existing_times = Counter(
        key for value in report_values if (key := _report_transaction_time_key(value))
    )
    existing_counts = Counter(
        key for value in report_values if (key := _accounting_date_key(value))
    )
    new_rows: list[list[str]] = []
    for row in table_target_rows(table):
        time_key = transaction_time_key(row)
        key = _accounting_date_key(row[0])
        if existing_times[time_key] > 0:
            existing_times[time_key] -= 1
            report_date = time_key[:10]
            if existing_counts[report_date] > 0:
                existing_counts[report_date] -= 1
        elif existing_counts[key] > 0:
            existing_counts[key] -= 1
        elif key:
            new_rows.append(row)
    return CapturedTable(headers=table.headers, rows=new_rows)


def read_and_filter(table: CapturedTable, area: str, bank: str) -> CapturedTable:
    target = load_sheet_target(area, bank)
    credentials = Credentials.from_service_account_info(load_google_credentials(), scopes=SCOPES)
    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(target.spreadsheet_id).get_worksheet_by_id(target.worksheet_gid)
    if bank == "yuanta":
        # 財報 B 欄（交易日期時間）的日期對應「元大銀行-區域」A 欄。
        return filter_yuanta_report_rows(table, worksheet.get("B2:B"))
    # 財報 B 欄是交易時間；轉成銀行清單的 A/B 結構後比對。
    existing_rows = [["", row[0]] for row in worksheet.get("B2:B") if row]
    return filter_existing_rows(table, existing_rows)


def _report_amount(value: str) -> int | float | str:
    normalized = _normalize_amount(value)
    if not normalized:
        return ""
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return value
    return int(number) if number == number.to_integral_value() else float(number)


def build_financial_report_rows(
    table: CapturedTable,
    *,
    first_sequence: int,
    include_bank_column: bool = True,
) -> list[list[Any]]:
    """銀行 A:G 轉為財報：序號、交易日、帳務日、說明、行庫、支出、存入、餘額、備註。"""
    output: list[list[Any]] = []
    for offset, row in enumerate(table_target_rows(table)):
        values: list[Any] = [
            first_sequence + offset,
            row[1],
            row[0],
            row[2],
        ]
        if include_bank_column:
            values.append("")
        values.extend([
            _report_amount(row[3]),
            _report_amount(row[4]),
            _report_amount(row[5]),
            row[6],
        ])
        output.append(values)
    return output


def sync_financial_report(table: CapturedTable, area: str, bank: str) -> int:
    """將已確認未登記的明細同步追加至對應區域財報。"""
    if not table.rows:
        return 0
    target = load_sheet_target(area, bank)
    credentials = Credentials.from_service_account_info(load_google_credentials(), scopes=SCOPES)
    worksheet = (
        gspread.authorize(credentials)
        .open_by_key(target.spreadsheet_id)
        .get_worksheet_by_id(target.worksheet_gid)
    )
    existing = worksheet.get("A:I")
    headers = existing[0] if existing else []
    normalized_headers = [_normalize_text(value).replace("\n", "") for value in headers]
    include_bank_column = any("交易行庫" in value for value in normalized_headers)
    transaction_column = 1
    last_row = 1
    sequences: list[int] = []
    for row_number, row in enumerate(existing[1:], start=2):
        if len(row) > transaction_column and str(row[transaction_column]).strip():
            last_row = row_number
        if row and str(row[0]).strip().isdigit():
            sequences.append(int(str(row[0]).strip()))
    rows = build_financial_report_rows(
        table,
        first_sequence=(max(sequences, default=0) + 1),
        include_bank_column=include_bank_column,
    )
    end_column = "I" if include_bank_column else "H"
    start_row = last_row + 1
    worksheet.update(
        range_name=f"A{start_row}:{end_column}{start_row + len(rows) - 1}",
        values=rows,
        value_input_option="RAW",
    )
    return len(rows)


def sync_bank_master_sheet(
    table: CapturedTable,
    area: str,
    bank: str,
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    service = service or get_sheets_service()
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    sheet_ids = {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in meta.get("sheets", [])
    }
    titles = set(sheet_ids)
    bank_name = {"fubon": "富邦銀行", "yuanta": "元大銀行"}.get(bank)
    if not bank_name:
        raise ValueError(f"不支援的銀行：{bank}")
    missing = [f"{bank_name}-{name}" for name in BANK_AREAS if f"{bank_name}-{name}" not in titles]
    if missing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [
                {"addSheet": {"properties": {"title": title, "gridProperties": {"frozenRowCount": 1}}}}
                for title in missing
            ]},
        ).execute()
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        sheet_ids = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in meta.get("sheets", [])
        }
    for name in BANK_AREAS:
        title = f"{bank_name}-{name}"
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
    title = f"{bank_name}-{area}"
    # 此分頁是「目前未登記清單」而非歷史資料，每次以最新比對結果重建。
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A2:G",
        body={},
    ).execute()
    write_rows = [list(row) for row in table.rows]
    if bank == "fubon":
        for row in write_rows:
            for column in range(3, 6):
                row[column] = _integer_amount(row[column])
    if write_rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A2:G{len(write_rows) + 1}",
            # 保留銀行原始日期文字，避免 A/B 欄被轉成 2026/8/6 與 46240.36983。
            valueInputOption="RAW",
            body={"values": write_rows},
        ).execute()
    if bank in ("fubon", "yuanta"):
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_ids[title],
                        "startRowIndex": 1,
                        "startColumnIndex": 3,
                        "endColumnIndex": 6,
                    },
                    "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                    "fields": "userEnteredFormat.horizontalAlignment",
                }
            }]},
        ).execute()
    return len(table.rows)


def sync_fubon_master_sheet(
    table: CapturedTable,
    area: str,
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    return sync_bank_master_sheet(
        table, area, "fubon", service=service, spreadsheet_id=spreadsheet_id
    )


def sync_yuanta_master_sheet(
    table: CapturedTable,
    area: str,
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    return sync_bank_master_sheet(
        table, area, "yuanta", service=service, spreadsheet_id=spreadsheet_id
    )


def sync_yuanta_salary_status(
    rows: list[list[str]],
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    service = service or get_sheets_service()
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    title = "元大銀行-薪資付款狀態"
    headers = ["區域", "收款人資料", "付款金額", "摘要", "手續費", "處理狀態/錯誤代碼"]
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    sheet_ids = {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in meta.get("sheets", [])
    }
    if title not in sheet_ids:
        response = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title, "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
        sheet_ids[title] = response["replies"][0]["addSheet"]["properties"]["sheetId"]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1:F1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A2:F",
        body={},
    ).execute()
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A2:F{len(rows) + 1}",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_ids[title], "startRowIndex": 1, "startColumnIndex": column, "endColumnIndex": column + 1},
                    "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                    "fields": "userEnteredFormat.horizontalAlignment",
                }
            }
            for column in (2, 4)
        ]},
    ).execute()
    return len(rows)
