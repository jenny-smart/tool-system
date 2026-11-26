"""內勤工作表單換年整理（2026-11-26 Jenny）。

1. 補分頁：新年度內勤工作表單缺少的固定分頁（KEEP_EXTRA_SHEETS）從前一年度檔複製過去，名稱不變。
2. 評價預約：新年度檔只留新年度的資料。
   - 從前一年度檔整頁複製「評價預約」（保留連結、格式、最新內容），貼到新年度檔的「評價預約」
     （分頁本身不刪，檔內其他公式的參照不會斷）。
   - 刪掉預約日期（F 欄）不是新年度（或之後）的列；沒寫年份的列視為前一年度，一併刪除。
   - 區塊標題列（只有 C 欄有字，例如「10月之後預約」）與空白分隔列保留；
     區塊名稱含「定期」的整段（單次付款定期、定期VIP每月需通知）是長期名單，整段保留。
   建議 1/1 執行（前一年度檔的評價預約到 12/31 都還在新增）。
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

RATING_SHEET = "評價預約"
# 新年度內勤工作表單要另外保留／補上的分頁（生成新年度 OFFICE_KEEP_SHEETS 也包含這些）
KEEP_EXTRA_SHEETS = ["VIP名單", "系統執行Log", "捷運站表", "消毒除蟲同步(勿動)"]
RECURRING_SECTION_MARK = "定期"
DATE_COL, NAME_COL = 5, 2   # F 欄預約日期、C 欄姓名（0 起算）

_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_ROC = re.compile(r"(?<!\d)(1[0-4]\d)\s*/\s*\d{1,2}\s*/\s*\d{1,2}")


def _cell(row: List[str], idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) else ""


def row_year(row: List[str]) -> Optional[int]:
    """預約日期（F 欄）的年份：2027-01-23、2027/1/23、民國 116/01/23；沒寫年份回傳 None。"""
    text = _cell(row, DATE_COL)
    m = _YEAR.search(text)
    if m:
        return int(m.group(1))
    m = _ROC.search(text)
    return int(m.group(1)) + 1911 if m else None


def is_section_header(row: List[str]) -> bool:
    filled = [i for i, v in enumerate(row) if str(v).strip()]
    return filled == [NAME_COL]


def rows_to_delete(values: List[List[str]], new_year: int) -> List[int]:
    """回傳要刪的列號（1 起算，由小到大）；第 1 列是標題不動。"""
    delete, recurring = [], False
    for row_no, row in enumerate(values[1:], start=2):
        if not any(str(v).strip() for v in row):
            continue
        if is_section_header(row):
            recurring = RECURRING_SECTION_MARK in _cell(row, NAME_COL)
            continue
        if recurring:
            continue
        year = row_year(row)
        if year is None or year < new_year:
            delete.append(row_no)
    return delete


def _runs(rows: List[int]) -> List[tuple]:
    """[3,4,5,9] → [(3,6),(9,10)]（0 起算 start、end 不含）供 deleteDimension 用，由下往上刪。"""
    runs, start, prev = [], None, None
    for r in rows:
        if start is None:
            start = prev = r
        elif r == prev + 1:
            prev = r
        else:
            runs.append((start - 1, prev))
            start = prev = r
    if start is not None:
        runs.append((start - 1, prev))
    return sorted(runs, reverse=True)


def _sheet_props(sheets, spreadsheet_id: str) -> Dict[str, dict]:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                     fields="sheets.properties(sheetId,title,index,gridProperties)").execute()
    return {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}


def _copy_sheet(sheets, src_id: str, sheet_id: int, dst_id: str, title: str, index: Optional[int]) -> int:
    copied = sheets.spreadsheets().sheets().copyTo(
        spreadsheetId=src_id, sheetId=sheet_id, body={"destinationSpreadsheetId": dst_id}).execute()
    props = {"sheetId": copied["sheetId"], "title": title}
    fields = "title"
    if index is not None:
        props["index"], fields = index, "title,index"
    sheets.spreadsheets().batchUpdate(spreadsheetId=dst_id, body={"requests": [
        {"updateSheetProperties": {"properties": props, "fields": fields}}]}).execute()
    return copied["sheetId"]


def copy_missing_tabs(sheets, prev_id: str, new_id: str, titles: List[str] = None,
                      dry_run: bool = False, log: Callable[[str], None] = print) -> List[str]:
    """前一年度有、新年度沒有的分頁複製過去；回傳補上的分頁名稱。"""
    prev, new = _sheet_props(sheets, prev_id), _sheet_props(sheets, new_id)
    added = []
    for title in titles or KEEP_EXTRA_SHEETS:
        if title in new:
            continue
        if title not in prev:
            log(f"  前一年度檔沒有「{title}」，略過")
            continue
        if not dry_run:
            _copy_sheet(sheets, prev_id, prev[title]["sheetId"], new_id, title, None)
        added.append(title)
        log(f"  {'（預覽）' if dry_run else ''}補上分頁「{title}」")
    return added


def rollover_rating(sheets, prev_id: str, new_id: str, new_year: int,
                    dry_run: bool = False, log: Callable[[str], None] = print) -> Dict[str, int]:
    """評價預約：從前一年度整頁複製到新年度檔，只留新年度資料。"""
    prev, new = _sheet_props(sheets, prev_id), _sheet_props(sheets, new_id)
    if RATING_SHEET not in prev:
        raise RuntimeError(f"前一年度檔沒有「{RATING_SHEET}」分頁")
    values = sheets.spreadsheets().values().get(
        spreadsheetId=prev_id, range=f"'{RATING_SHEET}'!A1:Z").execute().get("values", [])
    delete = rows_to_delete(values, new_year)
    kept = sum(1 for row in values[1:] if any(str(v).strip() for v in row)) - len(delete)
    log(f"  {RATING_SHEET}：前一年度 {len(values) - 1} 列，刪 {len(delete)} 列（非 {new_year} 年），留 {kept} 列")
    if dry_run:
        return {"deleted": len(delete), "kept": kept}
    old = new.get(RATING_SHEET)
    temp_title = f"{RATING_SHEET}_換年暫存"
    temp_id = _copy_sheet(sheets, prev_id, prev[RATING_SHEET]["sheetId"], new_id, temp_title, None)
    prune = [{"deleteDimension": {"range": {"sheetId": temp_id, "dimension": "ROWS",
                                            "startIndex": s, "endIndex": e}}} for s, e in _runs(delete)]
    if not old:  # 新年度檔沒有評價預約：暫存頁直接改名
        prune.append({"updateSheetProperties": {"properties": {"sheetId": temp_id, "title": RATING_SHEET},
                                                "fields": "title"}})
        sheets.spreadsheets().batchUpdate(spreadsheetId=new_id, body={"requests": prune}).execute()
        return {"deleted": len(delete), "kept": kept}
    # 保留原分頁（sheetId 不變，檔內其他公式參照不會斷）：清空後把暫存頁整頁貼上（含連結、格式），再刪暫存頁
    src_rows = int(prev[RATING_SHEET].get("gridProperties", {}).get("rowCount", 1000)) - len(delete)
    src_cols = int(prev[RATING_SHEET].get("gridProperties", {}).get("columnCount", 26))
    grid = old.get("gridProperties", {})
    requests = prune + [{"updateCells": {"range": {"sheetId": old["sheetId"]},
                                         "fields": "userEnteredValue,userEnteredFormat,textFormatRuns,note"}}]
    for dim, need, have in (("ROWS", src_rows, int(grid.get("rowCount", 1000))),
                            ("COLUMNS", src_cols, int(grid.get("columnCount", 26)))):
        if need > have:
            requests.append({"appendDimension": {"sheetId": old["sheetId"], "dimension": dim, "length": need - have}})
    requests += [
        {"copyPaste": {"source": {"sheetId": temp_id, "startRowIndex": 0, "endRowIndex": src_rows,
                                  "startColumnIndex": 0, "endColumnIndex": src_cols},
                       "destination": {"sheetId": old["sheetId"], "startRowIndex": 0, "endRowIndex": src_rows,
                                       "startColumnIndex": 0, "endColumnIndex": src_cols},
                       "pasteType": "PASTE_NORMAL"}},
        {"deleteSheet": {"sheetId": temp_id}},
    ]
    sheets.spreadsheets().batchUpdate(spreadsheetId=new_id, body={"requests": requests}).execute()
    return {"deleted": len(delete), "kept": kept}


def prune_rating_in_place(sheets, file_id: str, new_year: int,
                          dry_run: bool = False, log: Callable[[str], None] = print) -> int:
    """生成新年度剛複製好的內勤工作表單：直接刪掉評價預約裡非新年度的列。回傳刪除列數。"""
    props = _sheet_props(sheets, file_id).get(RATING_SHEET)
    if not props:
        return 0
    values = sheets.spreadsheets().values().get(
        spreadsheetId=file_id, range=f"'{RATING_SHEET}'!A1:Z").execute().get("values", [])
    delete = rows_to_delete(values, new_year)
    log(f"  {RATING_SHEET}：只留 {new_year} 年資料，刪 {len(delete)} 列")
    if delete and not dry_run:
        sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
            {"deleteDimension": {"range": {"sheetId": props["sheetId"], "dimension": "ROWS",
                                           "startIndex": s, "endIndex": e}}} for s, e in _runs(delete)]}).execute()
    return len(delete)
