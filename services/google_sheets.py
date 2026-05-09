from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import gspread
from gspread.utils import a1_to_rowcol, rowcol_to_a1

from config.vip_config import TIMEZONE, DEFAULT_MONTHLY_LOG_ITEMS


def now_tw() -> str:
    # Streamlit server may run UTC. Use zoneinfo if available.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y/%m/%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def col_to_num(col: str) -> int:
    return a1_to_rowcol(f"{col.upper()}1")[1]


def num_to_col(num: int) -> str:
    return rowcol_to_a1(1, num).rstrip("1")


def col_values_until_blank(ws: gspread.Worksheet, col_num: int, start_row: int = 2) -> list[Any]:
    values = ws.col_values(col_num)[start_row - 1:]
    out = []
    for v in values:
        if v == "":
            break
        out.append(v)
    return out


class SheetsService:
    def __init__(self, gc: gspread.Client, sheets_api):
        self.gc = gc
        self.sheets_api = sheets_api

    def open_by_id(self, spreadsheet_id: str) -> gspread.Spreadsheet:
        return self.gc.open_by_key(spreadsheet_id)

    def worksheet(self, sh: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
        try:
            return sh.worksheet(name)
        except gspread.WorksheetNotFound:
            return sh.add_worksheet(title=name, rows=1000, cols=80)

    def clear_data_area(self, ws: gspread.Worksheet, end_col: str, start_row: int = 2) -> None:
        last = max(ws.row_count, ws.row_values(1).__len__(), 1000)
        ws.batch_clear([f"A{start_row}:{end_col}{last}"])

    def get_values(self, ws: gspread.Worksheet, a1: str) -> list[list[Any]]:
        return ws.get(a1)

    def update_values(self, ws: gspread.Worksheet, start_a1: str, values: list[list[Any]], user_entered: bool = False) -> None:
        if not values:
            return
        ws.update(start_a1, values, value_input_option="USER_ENTERED" if user_entered else "RAW")

    def paste_rows_at_a2(self, ws: gspread.Worksheet, rows: list[list[Any]]) -> None:
        if rows:
            self.update_values(ws, "A2", rows)

    def write_formula_and_copy_down(self, spreadsheet_id: str, sheet_id: int, col: str, formula: str, start_row: int, row_count: int) -> None:
        if row_count <= 0:
            return
        col_num = col_to_num(col)
        first_a1 = rowcol_to_a1(start_row, col_num)
        body = {"values": [[formula if str(formula).startswith("=") else "=" + str(formula)]]}
        self.sheets_api.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=first_a1,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        if row_count == 1:
            return
        requests = [{
            "copyPaste": {
                "source": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": start_row,
                    "startColumnIndex": col_num - 1,
                    "endColumnIndex": col_num,
                },
                "destination": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": start_row - 1 + row_count,
                    "startColumnIndex": col_num - 1,
                    "endColumnIndex": col_num,
                },
                "pasteType": "PASTE_FORMULA",
            }
        }]
        self.sheets_api.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


class MasterLog:
    def __init__(self, sheets: SheetsService, master: gspread.Spreadsheet, monthly_sheet_name: str):
        self.sheets = sheets
        self.master = master
        self.ws = sheets.worksheet(master, monthly_sheet_name)
        self.ensure_structure()

    def ensure_structure(self) -> None:
        if not self.ws.acell("A1").value:
            self.ws.update("A1", [["項目"]])
        existing = set(self.ws.col_values(1)[1:])
        rows_to_add = [[item] for item in DEFAULT_MONTHLY_LOG_ITEMS if item not in existing]
        if rows_to_add:
            start = len(self.ws.col_values(1)) + 1
            self.ws.update(f"A{start}", rows_to_add)

    def ensure_period_col(self, period: str) -> int:
        headers = self.ws.row_values(1)
        if period in headers:
            return headers.index(period) + 1
        col = len(headers) + 1
        self.ws.update_cell(1, col, period)
        return col

    def ensure_item_row(self, item: str) -> int:
        items = self.ws.col_values(1)
        if item in items:
            return items.index(item) + 1
        row = len(items) + 1
        self.ws.update_cell(row, 1, item)
        return row

    def stamp(self, period: str, item: str, value: Any) -> None:
        row = self.ensure_item_row(item)
        col = self.ensure_period_col(period)
        self.ws.update_cell(row, col, value)

    def stamp_count_time(self, period: str, area: str, typ: str, step: str, count: int) -> None:
        self.stamp(period, f"{area}{typ}{step}筆數", count)
        self.stamp(period, f"{area}{typ}{step}時間", now_tw())

    def append_execution_log(self, sheet_name: str, rows: list[list[Any]]) -> None:
        ws = self.sheets.worksheet(self.master, sheet_name)
        if not ws.acell("A1").value:
            ws.update("A1", [["時間", "期別", "步驟", "區域", "類型", "訊息"]])
        ws.append_rows(rows, value_input_option="USER_ENTERED")
