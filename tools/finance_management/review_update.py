"""2026review｜調整年度預估欄的 SUMIF 公式範圍。

「2026目標及review檔」裡，每個地區自己的分頁上有一批統計列，公式長這樣：
  =SUMIF(C$1:N3,"*預估*",C3:N3)
  =SUMIF(B$1:Y2,"*預估*",B2:Y2)
即「掃 row1 標題含『預估』的欄位，加總同一列對應的值」。這批公式的起始欄
（C、B…）在不同列不一定一樣，結尾欄（N、Y、E…）也不一致——有些是舊期別
留下的，沒有隨每次換期別更新。

換期別時要做的事：把每一條這種 SUMIF 公式的「結尾欄」，改成「該分頁 row1
標題等於『{目標年月}預估』的那一欄」，起始欄跟列號都不動。結尾欄不能寫死
（不同分頁、不同月份，欄位位置都不同），要用 row1 標題動態找。

流程分兩步，故意不會一次到位直接寫入試算表：
  1. preview_review_formula_updates()：只讀取、不寫入，回傳「哪些儲存格、
     舊公式、新公式」的清單，先讓人確認調整方向對不對。
  2. apply_review_formula_updates()：把 preview 算出的清單實際寫回去，並在
     主控試算表的「2026review公式調整記錄」分頁，逐一記下每個被改動儲存格
     的時間／地區／期別／舊公式／新公式，方便事後查改了什麼、什麼時候改的。
這樣萬一抓錯欄位，不會直接改壞正式的年度 review 檔，改完也留得下紀錄。
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import resolve_review_location

TW_TZ = ZoneInfo("Asia/Taipei")
CHANGE_LOG_SHEET = "2026review公式調整記錄"
_CHANGE_LOG_HEADER = ["時間", "地區", "期別", "儲存格", "舊公式", "新公式"]


def _ensure_change_log_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if CHANGE_LOG_SHEET in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": CHANGE_LOG_SHEET}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{CHANGE_LOG_SHEET}'!A1:F1",
        valueInputOption="RAW",
        body={"values": [_CHANGE_LOG_HEADER]},
    ).execute()


def _log_changes(area: str, year_month: str, changes: list[dict[str, str]]) -> None:
    """把每一個被改動的儲存格，各記一列到「2026review公式調整記錄」分頁
    （時間／地區／期別／儲存格／舊公式／新公式），方便事後回頭查改了什麼、
    什麼時候改的。記錄本身失敗不影響主要功能。"""
    if not changes:
        return
    try:
        service = get_sheets_service()
        master_id = get_master_spreadsheet_id()
        _ensure_change_log_sheet(service, master_id)
        now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
        rows = [
            [now_text, area, year_month, c["cell"], c["old_formula"], c["new_formula"]]
            for c in changes
        ]
        service.spreadsheets().values().append(
            spreadsheetId=master_id,
            range=f"'{CHANGE_LOG_SHEET}'!A:F",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    except Exception:
        pass

_SUMIF_RE = re.compile(
    r'^=SUM[Ii][Ff]\(\s*\$?([A-Z]+)\$?1\s*:\s*\$?([A-Z]+)(\d+)\s*,'
    r'\s*"\*預估\*"\s*,'
    r'\s*\$?([A-Z]+)(\d+)\s*:\s*\$?([A-Z]+)(\d+)\s*\)$'
)


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _letter_to_index(letter: str) -> int:
    index = 0
    for ch in letter:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def _find_target_column(header_row: list[str], year_month: str) -> str:
    """在 row1 標題裡找「{年月}預估」對應的欄位英文字母。年月同時接受
    YYYY.MM 與 YY.MM 兩種寫法（有些欄位標題沒補齊到四位數年份）。"""
    short_year_month = year_month[2:] if len(year_month) == 7 else year_month  # 2026.07 -> 26.07
    targets = {f"{year_month}預估", f"{short_year_month}預估"}
    for idx, value in enumerate(header_row, start=1):
        if str(value).strip() in targets:
            return _column_letter(idx)
    raise RuntimeError(f"row1 標題找不到「{year_month}預估」（也試過「{short_year_month}預估」）")


def _rewrite_formula(formula: str, target_col: str) -> str | None:
    match = _SUMIF_RE.match(formula.strip())
    if not match:
        return None
    start_col1, _end_col1, row1, start_col2, row2, _end_col2, row3 = match.groups()
    if row1 != row2 or row1 != row3:
        return None  # 三個列號對不上，格式跟預期不符，跳過不硬改
    return f"=SUMIF({start_col1}$1:{target_col}{row1},\"*預估*\",{start_col2}{row2}:{target_col}{row3})"


def preview_review_formula_updates(area: str, year_month: str) -> list[dict[str, str]]:
    """只讀取、不寫入。回傳 [{'cell', 'old_formula', 'new_formula'}, ...]。

    year_month 格式 YYYY.MM，例如 "2026.07"。
    """
    spreadsheet_id, title = resolve_review_location(area)
    service = get_sheets_service()
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:ZZ",
        valueRenderOption="FORMULA",
    ).execute()
    values = res.get("values", [])
    if not values:
        return []

    header_row = values[0]
    target_col = _find_target_column(header_row, year_month)

    changes: list[dict[str, str]] = []
    for row_idx, row in enumerate(values[1:], start=2):
        for col_idx, cell_value in enumerate(row, start=1):
            text = str(cell_value)
            if not text.startswith("=") or "預估" not in text:
                continue
            new_formula = _rewrite_formula(text, target_col)
            if new_formula is None or new_formula == text:
                continue
            changes.append({
                "cell": f"{_column_letter(col_idx)}{row_idx}",
                "old_formula": text,
                "new_formula": new_formula,
            })
    return changes


def apply_review_formula_updates(area: str, year_month: str) -> dict[str, int]:
    """套用 preview_review_formula_updates() 算出的調整，實際寫回試算表。"""
    log_execution("2026review公式調整", area, "開始", f"期別 {year_month}")
    try:
        changes = preview_review_formula_updates(area, year_month)
        if not changes:
            log_execution("2026review公式調整", area, "完成", "沒有需要調整的公式")
            return {"changed": 0}

        spreadsheet_id, title = resolve_review_location(area)
        service = get_sheets_service()
        data = [
            {"range": f"'{title}'!{c['cell']}", "values": [[c["new_formula"]]]}
            for c in changes
        ]
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
        _log_changes(area, year_month, changes)
    except Exception as exc:
        log_execution("2026review公式調整", area, "失敗", str(exc))
        raise
    log_execution("2026review公式調整", area, "完成", f"調整 {len(changes)} 個儲存格")
    return {"changed": len(changes)}
