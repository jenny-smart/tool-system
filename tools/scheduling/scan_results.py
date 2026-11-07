"""排程盤點結果：雲端排程盤點完存到主控表「排程盤點結果」，Tools App 開著時跳視窗，按「前往處理」
直接停在那次盤點結果，勾選後接著執行（不用重新盤點）。

2026-11-06 Jenny：【整合執行】清潔異動盤點、富邦內部請款盤點要像富邦明細下載跳驗證碼一樣，
跳視窗讓我接續作業。

每個功能只留最新一筆；狀態：待處理 → 已開啟（按了前往處理或稍後）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

SHEET_NAME = "排程盤點結果"
HEADER = ["功能代號", "盤點時間", "摘要", "筆數", "狀態", "資料"]
PENDING = "待處理"
OPENED = "已開啟"
CHUNK = 45000  # 一格最多 50,000 字，JSON 拆在 F 欄之後
MAX_CHUNKS = 40
TW = ZoneInfo("Asia/Taipei")

# 功能代號 → (網頁系統, 功能名稱, 頁面暫存盤點結果的 session_state key, 視窗標題)
PAGES: dict[str, tuple[str, str, str, str]] = {
    "change_order_scan": ("財務管理", "【整合執行】清潔異動已備妥", "change_order_batch_items", "清潔異動"),
    "fubon_pending_check": ("財務管理", "【整合執行】富邦內部請款", "internal_payment_batch_items", "富邦內部請款"),
}


def _service_and_id():
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

    return get_sheets_service(), get_master_spreadsheet_id()


def _ensure_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    if any(s["properties"]["title"] == SHEET_NAME for s in meta.get("sheets", [])):
        return
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
        {"addSheet": {"properties": {"title": SHEET_NAME, "gridProperties": {"columnCount": 6 + MAX_CHUNKS}}}}]}).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A1:F1", valueInputOption="RAW",
        body={"values": [HEADER]}).execute()


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _rows(service, spreadsheet_id: str, columns: str = "A:E") -> list[list[Any]]:
    return service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!{columns}").execute().get("values", [])


def _find_row(rows: list[list[Any]], key: str) -> Optional[int]:
    for offset, row in enumerate(rows[1:], start=2):
        if row and str(row[0]).strip() == key:
            return offset
    return None


def encode(items: list[dict]) -> list[str]:
    text = json.dumps(items, ensure_ascii=False, default=str, separators=(",", ":"))
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or ["[]"]
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"盤點結果太大（{len(text):,} 字），無法存到「{SHEET_NAME}」")
    return chunks


def decode(chunks: list[Any]) -> list[dict]:
    return json.loads("".join(str(c) for c in chunks) or "[]")


def save(key: str, items: list[dict], summary: str, *, now: Optional[datetime] = None, service=None,
         spreadsheet_id: str = "") -> None:
    """存最新一次排程盤點結果（同一功能覆蓋上一筆），狀態＝待處理。"""
    if not service:
        service, spreadsheet_id = _service_and_id()
    _ensure_sheet(service, spreadsheet_id)
    rows = _rows(service, spreadsheet_id, "A:A")
    row_no = _find_row(rows, key) or len(rows) + 1
    chunks = encode(items)
    when = (now or datetime.now(TW)).strftime("%Y/%m/%d %H:%M")
    values = [key, when, summary[:2000], len(items), PENDING] + chunks
    # 先清掉舊的 JSON 分段，再寫入
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A{row_no}:{_col(5 + MAX_CHUNKS)}{row_no}").execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A{row_no}", valueInputOption="RAW",
        body={"values": [values]}).execute()
    # 2026-11-06 Jenny：排程盤點也記到執行 Log，才看得出有跑
    from tools.common import unified_log

    page = PAGES.get(key)
    unified_log.write(system=page[0] if page else "", function=(page[1] if page else key) + "（排程盤點）",
                      run_type="排程", status="成功", message=f"{summary}；等你在 Tools App 確認後執行")


def pending(*, today: Optional[str] = None, service=None, spreadsheet_id: str = "") -> list[dict]:
    """今天、狀態待處理的盤點結果（不讀 JSON）。"""
    if not service:
        service, spreadsheet_id = _service_and_id()
    try:
        rows = _rows(service, spreadsheet_id)
    except Exception:
        return []  # 分頁還沒建立
    today = today or datetime.now(TW).strftime("%Y/%m/%d")
    result = []
    for offset, row in enumerate(rows[1:], start=2):
        cells = [str(c).strip() for c in row] + [""] * (5 - len(row))
        if cells[0] in PAGES and cells[4] == PENDING and cells[1].startswith(today):
            result.append({"key": cells[0], "time": cells[1], "summary": cells[2], "count": cells[3], "row": offset})
    return result


def latest_today(key: str, *, today: Optional[str] = None, service=None, spreadsheet_id: str = "") -> Optional[dict]:
    """今天這個功能的排程盤點結果（不管有沒有開過），給頁面「載入排程盤點結果」按鈕用。"""
    if not service:
        service, spreadsheet_id = _service_and_id()
    try:
        rows = _rows(service, spreadsheet_id)
    except Exception:
        return None
    today = today or datetime.now(TW).strftime("%Y/%m/%d")
    row_no = _find_row(rows, key)
    if not row_no:
        return None
    cells = [str(c).strip() for c in rows[row_no - 1]] + [""] * 5
    if not cells[1].startswith(today):
        return None
    return {"key": key, "time": cells[1], "summary": cells[2], "count": cells[3], "status": cells[4], "row": row_no}


def load_items(key: str, *, service=None, spreadsheet_id: str = "") -> list[dict]:
    if not service:
        service, spreadsheet_id = _service_and_id()
    rows = _rows(service, spreadsheet_id, f"A:{_col(5 + MAX_CHUNKS)}")
    row_no = _find_row(rows, key)
    if not row_no:
        return []
    return decode(rows[row_no - 1][5:])


def mark(key: str, status: str = OPENED, *, service=None, spreadsheet_id: str = "") -> None:
    if not service:
        service, spreadsheet_id = _service_and_id()
    row_no = _find_row(_rows(service, spreadsheet_id, "A:A"), key)
    if row_no:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!E{row_no}", valueInputOption="RAW",
            body={"values": [[status]]}).execute()
