"""
專員訂單：給專員作業系統（GAS）「加減時」選訂單用的清單。

每天「外場訂單」跑完後，於各區當年度「內勤工作表單」重寫「專員訂單」工作表：
- 範圍：上個月 1 日～今天（服務日期 ≤ 今天的訂單，派班專員已確定；今天以後可能再改，不列）
- 本月資料取今天的訂單匯出檔；上個月資料取上個月最後一天的匯出檔（Drive 已有，不再多打後台）
- 只留表單需要的欄位；每天整張重寫，大小固定約兩個月，不會累積
不動「訂單資料」（其他功能也在用）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

SHEET_NAME = "專員訂單"
FIELDS = [
    "訂單編號", "服務日期", "服務時間", "服務時數", "客戶姓名", "聯絡電話",
    "服務專員", "購買項目", "服務細項", "取消狀態",
]


def normalize_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    m = re.match(r"\s*(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", str(value or ""))
    if not m:
        return ""
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).strftime("%Y/%m/%d")
    except ValueError:
        return ""


def date_window(date_key: str) -> tuple[str, str]:
    """date_key=YYYYMMDD → (上個月1日, 今天)，格式 YYYY/MM/DD。"""
    today = datetime.strptime(date_key, "%Y%m%d").date()
    first_this = today.replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    return first_prev.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")


def previous_month_file_dates(date_key: str, tries: int = 5) -> list[str]:
    """上個月最後一天往前幾天的檔名日期（最後一天的匯出檔不存在時往前找）。"""
    today = datetime.strptime(date_key, "%Y%m%d").date()
    last_prev = today.replace(day=1) - timedelta(days=1)
    return [(last_prev - timedelta(days=i)).strftime("%Y%m%d") for i in range(tries)]


def pick_rows(export_values: list[list[Any]], start: str, end: str) -> list[dict[str, str]]:
    """匯出檔（含表頭）→ 服務日期在 [start, end] 的訂單。"""
    if not export_values:
        return []
    header = [str(h).strip() for h in export_values[0]]
    idx = {f: (header.index(f) if f in header else None) for f in FIELDS}
    if idx["訂單編號"] is None or idx["服務日期"] is None:
        raise RuntimeError("訂單匯出缺少「訂單編號」或「服務日期」欄位")
    out = []
    for row in export_values[1:]:
        def cell(f):
            i = idx[f]
            return "" if i is None or i >= len(row) or row[i] is None else str(row[i]).strip()
        d = normalize_date(cell("服務日期"))
        if not cell("訂單編號") or not d or not (start <= d <= end):
            continue
        rec = {f: cell(f) for f in FIELDS}
        rec["服務日期"] = d
        out.append(rec)
    return out


def build_staff_orders(this_month_export: list[list[Any]], prev_month_export: list[list[Any]] | None,
                       date_key: str) -> list[list[str]]:
    """組出「專員訂單」內容（含表頭）。同一訂單編號＋服務日期以今天的匯出為準。"""
    start, end = date_window(date_key)
    today = datetime.strptime(date_key, "%Y%m%d").date()
    first_this = today.replace(day=1).strftime("%Y/%m/%d")
    prev_end = (today.replace(day=1) - timedelta(days=1)).strftime("%Y/%m/%d")
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for rec in pick_rows(prev_month_export or [], start, prev_end):
        rows[(rec["訂單編號"], rec["服務日期"])] = rec
    for rec in pick_rows(this_month_export, start, end):
        if rec["服務日期"] >= first_this or (rec["訂單編號"], rec["服務日期"]) not in rows:
            rows[(rec["訂單編號"], rec["服務日期"])] = rec
    body = sorted(rows.values(), key=lambda r: (r["服務日期"], r["服務時間"], r["訂單編號"]))
    return [FIELDS] + [[r[f] for f in FIELDS] for r in body]


def write_staff_orders(sheets, spreadsheet_id: str, values: list[list[str]], log=print) -> int:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    if SHEET_NAME not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
            {"addSheet": {"properties": {"title": SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}}
        ]}).execute()
    rng = f"'{SHEET_NAME}'!A:{chr(64 + len(FIELDS))}"
    sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=rng, body={}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW", body={"values": values},
    ).execute()
    log(f"專員訂單：已寫入 {len(values) - 1} 筆")
    return len(values) - 1
