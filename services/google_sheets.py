from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
from gspread import Spreadsheet, Worksheet


TZ_FORMAT = "%Y/%m/%d %H:%M:%S"


def now_text() -> str:
    return datetime.now().strftime(TZ_FORMAT)


class SheetsService:
    def __init__(self, gc: gspread.Client):
        self.gc = gc

    def open_by_id(self, spreadsheet_id: str) -> Spreadsheet:
        return self.gc.open_by_key(spreadsheet_id)

    def get_or_create_ws(
        self,
        spreadsheet: Spreadsheet,
        title: str,
        rows: int = 1000,
        cols: int = 50,
    ) -> Worksheet:
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def clear_from_row(
        self,
        ws: Worksheet,
        start_row: int = 2,
        start_col: int = 1,
        end_col: Optional[int] = None,
    ) -> None:
        last_row = ws.row_count
        if end_col is None:
            end_col = ws.col_count

        if last_row < start_row:
            return

        start_a1 = rowcol_to_a1(start_row, start_col)
        end_a1 = rowcol_to_a1(last_row, end_col)
        ws.batch_clear([f"{start_a1}:{end_a1}"])

    def write_values(
        self,
        ws: Worksheet,
        start_row: int,
        start_col: int,
        values: List[List[Any]],
    ) -> None:
        if not values:
            return

        end_row = start_row + len(values) - 1
        end_col = start_col + max(len(r) for r in values) - 1

        range_name = f"{rowcol_to_a1(start_row, start_col)}:{rowcol_to_a1(end_row, end_col)}"
        ws.update(range_name, values, value_input_option="USER_ENTERED")

    def read_all_values(self, ws: Worksheet) -> List[List[Any]]:
        return ws.get_all_values()

    def read_range(self, ws: Worksheet, range_name: str) -> List[List[Any]]:
        return ws.get(range_name)


class MonthlyLog:
    """
    主控表「月度作業紀錄」用。

    這版重點：
    1. 初始化時只讀一次標題列與項目欄
    2. ensure_period_col / ensure_item_row 使用快取
    3. stamp_many 可批次寫入，減少 Google Sheets API 次數
    """

    def __init__(self, worksheet: Worksheet):
        self.ws = worksheet

        self.headers: List[str] = self.ws.row_values(1)
        self.items: List[str] = self.ws.col_values(1)

        if not self.headers:
            self.ws.update("A1", [["項目"]])
            self.headers = ["項目"]

        if not self.items:
            self.ws.update("A1", [["項目"]])
            self.items = ["項目"]

        if self.headers[0] != "項目":
            self.ws.update_cell(1, 1, "項目")
            self.headers[0] = "項目"

        if self.items[0] != "項目":
            self.ws.update_cell(1, 1, "項目")
            self.items[0] = "項目"

        self.period_col_cache: Dict[str, int] = {}
        self.item_row_cache: Dict[str, int] = {}

        for idx, header in enumerate(self.headers, start=1):
            if header:
                self.period_col_cache[str(header)] = idx

        for idx, item in enumerate(self.items, start=1):
            if item:
                self.item_row_cache[str(item)] = idx

    def ensure_period_col(self, period: str) -> int:
        period = str(period).strip()

        if period in self.period_col_cache:
            return self.period_col_cache[period]

        col = len(self.headers) + 1
        self.ws.update_cell(1, col, period)

        self.headers.append(period)
        self.period_col_cache[period] = col
        return col

    def ensure_item_row(self, item: str) -> int:
        item = str(item).strip()

        if item in self.item_row_cache:
            return self.item_row_cache[item]

        row = len(self.items) + 1
        self.ws.update_cell(row, 1, item)

        self.items.append(item)
        self.item_row_cache[item] = row
        return row

    def stamp(self, period: str, item: str, value: Any) -> None:
        row = self.ensure_item_row(item)
        col = self.ensure_period_col(period)
        self.ws.update_cell(row, col, value)

    def stamp_many(self, period: str, data: Dict[str, Any]) -> None:
        """
        一次寫入多個項目。
        data 格式：
        {
            "台北儲值金結算轉檔筆數": 100,
            "台北儲值金結算轉檔時間": "2026/05/10 07:30:00",
        }
        """
        if not data:
            return

        col = self.ensure_period_col(period)

        updates = []
        for item, value in data.items():
            row = self.ensure_item_row(item)
            updates.append(
                {
                    "range": rowcol_to_a1(row, col),
                    "values": [[value]],
                }
            )

        if updates:
            self.ws.batch_update(updates, value_input_option="USER_ENTERED")

    def stamp_count_time(
        self,
        period: str,
        area: str,
        typ: str,
        step: str,
        count: Any,
        timestamp: Optional[str] = None,
    ) -> None:
        """
        例如：
        台北儲值金結算轉檔筆數
        台北儲值金結算轉檔時間
        """
        if timestamp is None:
            timestamp = now_text()

        self.stamp_many(
            period,
            {
                f"{area}{typ}{step}筆數": count,
                f"{area}{typ}{step}時間": timestamp,
            },
        )

    def stamp_link(
        self,
        period: str,
        item: str,
        url: str,
    ) -> None:
        self.stamp(period, item, url)


class FormulaSettings:
    """
    主控表「公式設定」用。

    建議欄位：
    啟用 | 類型 | 區域 | 目標頁籤 | 目標欄位 | 公式 | 套用起始列 | 套用完成時間 | 套用筆數 | 備註
    """

    def __init__(self, worksheet: Worksheet):
        self.ws = worksheet

    def read_enabled(self) -> List[Dict[str, Any]]:
        values = self.ws.get_all_values()
        if len(values) < 2:
            return []

        headers = values[0]
        rows = []

        for idx, row in enumerate(values[1:], start=2):
            data = dict(zip(headers, row))

            enabled = str(data.get("啟用", "")).strip().upper()
            if enabled not in {"TRUE", "Y", "YES", "1", "是"}:
                continue

            data["_row"] = idx
            rows.append(data)

        return rows

    def stamp_formula_result(
        self,
        row: int,
        count: int,
        timestamp: Optional[str] = None,
    ) -> None:
        if timestamp is None:
            timestamp = now_text()

        headers = self.ws.row_values(1)

        time_col = find_header_col(headers, "套用完成時間")
        count_col = find_header_col(headers, "套用筆數")

        updates = []

        if time_col:
            updates.append(
                {
                    "range": rowcol_to_a1(row, time_col),
                    "values": [[timestamp]],
                }
            )

        if count_col:
            updates.append(
                {
                    "range": rowcol_to_a1(row, count_col),
                    "values": [[count]],
                }
            )

        if updates:
            self.ws.batch_update(updates, value_input_option="USER_ENTERED")


class ExecutionLog:
    """
    主控表「執行紀錄」用。
    """

    def __init__(self, worksheet: Worksheet):
        self.ws = worksheet
        headers = self.ws.row_values(1)

        if not headers:
            self.ws.update(
                "A1:E1",
                [["時間", "期別", "步驟", "狀態", "訊息"]],
            )

    def append(
        self,
        period: str,
        step: str,
        status: str,
        message: str,
    ) -> None:
        self.ws.append_row(
            [now_text(), period, step, status, message],
            value_input_option="USER_ENTERED",
        )


def find_header_col(headers: List[str], name: str) -> Optional[int]:
    for idx, header in enumerate(headers, start=1):
        if str(header).strip() == name:
            return idx
    return None


def col_to_number(col: str) -> int:
    col = str(col).strip().upper()
    num = 0
    for char in col:
        if not ("A" <= char <= "Z"):
            continue
        num = num * 26 + (ord(char) - ord("A") + 1)
    return num


def number_to_col(num: int) -> str:
    result = ""
    while num > 0:
        num, rem = divmod(num - 1, 26)
        result = chr(65 + rem) + result
    return result


def rowcol_to_a1(row: int, col: int) -> str:
    return f"{number_to_col(col)}{row}"

# ============================================================
# 舊版相容別名
# ============================================================

MasterLog = MonthlyLog
now_tw = now_text
col_to_num = col_to_number
