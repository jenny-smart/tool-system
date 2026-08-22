"""年度review｜調整年度預估欄的 SUMIF 公式範圍。

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
     主控試算表的「年度review公式調整記錄」分頁，逐一記下每個被改動儲存格
     的時間／地區／期別／舊公式／新公式，方便事後查改了什麼、什麼時候改的。
這樣萬一抓錯欄位，不會直接改壞正式的年度 review 檔，改完也留得下紀錄。

另外 group_hide_actual_columns()：把「當月」以及「隔月到全年」的「實際」欄位
（例如換到 202607 期別時的 O欄=2026.07實際、之後每月的實際欄，一路到年度
加總的「2026實際」欄，例如各區工作表的 AA欄）各自用 Google Sheets 的欄位
分組功能分組並隱藏——因為那些月份還沒發生，「實際」欄位還沒有真實數字。
已經過去、已經有數字的月份「實際」欄位不會動。年度review工作表跟各地區
工作表都適用；年度review工作表因為多一欄地區標示欄位，同名的「實際」／
「預估」標題會落在不同欄位字母，但因為都是照 row1 標題文字動態比對，不用
另外處理欄位位移。
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import (
    resolve_master_review_location,
    resolve_review_location,
)

MASTER_REVIEW_AREA = "年度review"  # 特殊地區代號：指「年度review工作表」這個跨地區彙整分頁


def _resolve_location(area: str) -> tuple[str, str]:
    if area == MASTER_REVIEW_AREA:
        return resolve_master_review_location()
    return resolve_review_location(area)


TW_TZ = ZoneInfo("Asia/Taipei")
CHANGE_LOG_SHEET = "年度review公式調整記錄"
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
    """把每一個被改動的儲存格，各記一列到「年度review公式調整記錄」分頁
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
            # RAW：舊公式／新公式是「=」開頭的文字，USER_ENTERED 會被 Sheets
            # 當成真正的公式執行，而且範圍剛好涵蓋記錄本身那一格，會自己形成
            # 循環參照（實際發生過：AA2 的公式寫進記錄的 E2，E2 又落在
            # B2:Y2 範圍內）。RAW 保證這兩欄永遠是純文字，不會被執行。
            valueInputOption="RAW",
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
    spreadsheet_id, title = _resolve_location(area)
    service = get_sheets_service()

    # 標題列（row1）跟表格內容分開讀：標題可能是公式算出來的月份文字
    # （例如用 TEXT()/EDATE() 產生），要讀「顯示結果」才拿得到真正的
    # 「2026.07預估」這種文字；表格內容則要讀「公式」原始文字，才能抓到
    # SUMIF 公式本身去改寫。
    header_res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!1:1",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    header_row = header_res.get("values", [[]])
    header_row = header_row[0] if header_row else []
    target_col = _find_target_column(header_row, year_month)

    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A2:ZZ",
        valueRenderOption="FORMULA",
    ).execute()
    values = [header_row] + res.get("values", [])
    if len(values) < 2:
        return []

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
    log_execution("年度review公式調整", area, "開始", f"期別 {year_month}")
    try:
        changes = preview_review_formula_updates(area, year_month)
        if not changes:
            log_execution("年度review公式調整", area, "完成", "沒有需要調整的公式")
            return {"changed": 0}

        spreadsheet_id, title = _resolve_location(area)
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
        log_execution("年度review公式調整", area, "失敗", str(exc))
        raise
    log_execution("年度review公式調整", area, "完成", f"調整 {len(changes)} 個儲存格")
    return {"changed": len(changes)}


_MONTH_HEADER_RE = re.compile(r"^(\d{2,4})\.(\d{1,2})實際$")
_YEAR_HEADER_RE = re.compile(r"^(\d{4})實際$")


def _actual_month_index(header_text: str) -> tuple[int, int] | None:
    """標題文字若是「YYYY.MM實際」或「YY.MM實際」，回傳 (年, 月)；否則 None。"""
    match = _MONTH_HEADER_RE.match(header_text.strip())
    if not match:
        return None
    year_text, month_text = match.groups()
    year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    return year, int(month_text)


def _actual_year_index(header_text: str) -> int | None:
    """標題文字若是「YYYY實際」（沒有月份，年度加總欄，例如 AA欄的「2026實際」），
    回傳年份；否則 None。"""
    match = _YEAR_HEADER_RE.match(header_text.strip())
    if not match:
        return None
    return int(match.group(1))


def group_hide_actual_columns(area: str, year_month: str) -> dict[str, object]:
    """把「當月實際」欄位，一路到「年度實際加總」欄位（例如「2026.07實際」到
    「2026實際」這一段連續範圍），各自分組並隱藏（用 Google Sheets 的欄位
    分組功能，不是單純隱藏——保留展開箭頭方便之後要看真實數字時自己展開）。
    已上市/已過去的月份「實際」欄位不動，因為那些是已經有真實數字的資料。
    年度review工作表跟各地區工作表都適用（area 決定要處理哪個分頁）。

    year_month 格式 YYYY.MM，例如 "2026.07"。
    """
    target_year, target_month = int(year_month[:4]), int(year_month[5:])
    spreadsheet_id, title = _resolve_location(area)
    service = get_sheets_service()

    header_res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!1:1",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    header_row = header_res.get("values", [[]])
    header_row = header_row[0] if header_row else []

    target_columns: list[int] = []  # 0-based
    for idx, value in enumerate(header_row):
        text = str(value)
        parsed = _actual_month_index(text)
        if parsed is not None:
            year, month = parsed
            if (year, month) >= (target_year, target_month):
                target_columns.append(idx)
            continue
        year_only = _actual_year_index(text)
        if year_only is not None and year_only >= target_year:
            target_columns.append(idx)

    if not target_columns:
        log_execution(
            "年度review欄位分組隱藏", area, "完成",
            f"期別 {year_month}：沒有找到 {year_month} 或之後的「實際」欄位",
        )
        return {"grouped": 0}

    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title),columnGroups)"
    ).execute()
    sheet_meta = next(s for s in meta.get("sheets", []) if s["properties"]["title"] == title)
    sheet_id = sheet_meta["properties"]["sheetId"]
    existing_ranges = {
        (g["range"]["startIndex"], g["range"]["endIndex"])
        for g in sheet_meta.get("columnGroups", [])
    }

    requests = []
    grouped = 0
    for col in target_columns:
        if (col, col + 1) in existing_ranges:
            continue  # 已經分組過了，不要重複疊加分組
        col_range = {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "startIndex": col,
            "endIndex": col + 1,
        }
        requests.append({"addDimensionGroup": {"range": col_range}})
        requests.append({
            "updateDimensionProperties": {
                "range": col_range,
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        })
        grouped += 1

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()

    log_execution(
        "年度review欄位分組隱藏", area, "完成",
        f"期別 {year_month}：分組隱藏 {grouped} 欄（跳過 {len(target_columns) - grouped} 個已分組過的）",
    )
    return {"grouped": grouped}


_FORECAST_MONTH_HEADER_RE = re.compile(r"^(\d{2,4})\.(\d{1,2})預估$")


def _forecast_month_index(header_text: str) -> tuple[int, int] | None:
    """標題文字若是「YYYY.MM預估」或「YY.MM預估」，回傳 (年, 月)；否則 None
    （年度加總欄「YYYY預估」沒有月份、沒有句點，不會match，維持一律不動）。"""
    match = _FORECAST_MONTH_HEADER_RE.match(header_text.strip())
    if not match:
        return None
    year_text, month_text = match.groups()
    year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    return year, int(month_text)


def group_hide_future_forecast_columns(area: str, year_month: str) -> dict[str, object]:
    """把「當月之後」的「預估」欄位分組隱藏；當月及之前的「預估」欄位維持顯示
    ——如果之前被誤分組隱藏過，會先解開（刪除分組＋取消隱藏）。年度加總的
    「YYYY預估」欄（沒有月份）永遠不動，一律維持顯示。

    跟 group_hide_actual_columns() 是相反方向：那邊隱藏「當月起」的實際欄
    （還沒發生、沒有真實數字），這邊隱藏「當月後」的預估欄（還不需要現在看，
    避免版面塞滿一整年的預估數字）。

    year_month 格式 YYYY.MM，例如 "2026.07"。
    """
    target_year, target_month = int(year_month[:4]), int(year_month[5:])
    spreadsheet_id, title = _resolve_location(area)
    service = get_sheets_service()

    header_res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!1:1",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    header_row = header_res.get("values", [[]])
    header_row = header_row[0] if header_row else []

    future_columns: list[int] = []  # 0-based，當月之後，要分組隱藏
    keep_visible_columns: list[int] = []  # 0-based，當月及之前，要確保顯示
    for idx, value in enumerate(header_row):
        parsed = _forecast_month_index(str(value))
        if parsed is None:
            continue
        year, month = parsed
        if (year, month) > (target_year, target_month):
            future_columns.append(idx)
        else:
            keep_visible_columns.append(idx)

    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title),columnGroups)"
    ).execute()
    sheet_meta = next(s for s in meta.get("sheets", []) if s["properties"]["title"] == title)
    sheet_id = sheet_meta["properties"]["sheetId"]
    existing_ranges = {
        (g["range"]["startIndex"], g["range"]["endIndex"])
        for g in sheet_meta.get("columnGroups", [])
    }

    requests = []
    ungrouped = 0
    for col in keep_visible_columns:
        if (col, col + 1) not in existing_ranges:
            continue  # 本來就沒被分組，不用動
        col_range = {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "startIndex": col,
            "endIndex": col + 1,
        }
        requests.append({"deleteDimensionGroup": {"range": col_range}})
        requests.append({
            "updateDimensionProperties": {
                "range": col_range,
                "properties": {"hiddenByUser": False},
                "fields": "hiddenByUser",
            }
        })
        ungrouped += 1

    grouped = 0
    for col in future_columns:
        if (col, col + 1) in existing_ranges:
            continue  # 已經分組過了，不要重複疊加分組
        col_range = {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "startIndex": col,
            "endIndex": col + 1,
        }
        requests.append({"addDimensionGroup": {"range": col_range}})
        requests.append({
            "updateDimensionProperties": {
                "range": col_range,
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        })
        grouped += 1

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()

    log_execution(
        "年度review欄位分組隱藏（預估）", area, "完成",
        f"期別 {year_month}：分組隱藏 {grouped} 個未來預估欄，解開 {ungrouped} 個當月/過去預估欄",
    )
    return {"grouped": grouped, "ungrouped": ungrouped}
