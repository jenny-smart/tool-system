"""工具包押金財報：統計／批次打勾／異常比對。

移植自「台北2026財報」／「台中財報」試算表 Apps Script 選單「📊 統計工具」
（onOpen 自訂選單），改為直接以 Sheets API 讀寫同一份試算表，取代 Google Sheet
端的 onOpen 選單，這樣就不用再手動開 Apps Script 編輯器操作。

原選單另有「依 J–M 重算 B1」一項，依需求已移除，未搬移到這裡。

欄位對應（與原 Apps Script 完全一致，1-based）：
  A=1  姓名
  B=2  應收押金餘額
  C=3  上課日期　D=4 結訓日期
  F=6 / G=7  退還相關欄位（日期，或「日期+備註」字串，例如「2026/1/12已匯款」）
  J=10 上課　K=11 退還　L=12 已退　M=13 不退（打勾狀態）
  N=14 異常標記（本模組的比對結果）
  U=21 備註　V=22 期別　W=23 收入　X=24 支出　Y=25 餘額（本模組不寫入 Y）
  Z=26 更新日期（每次寫入 U 欄新列時，同步在 Z 欄註記當天日期）

要讓這裡的功能找得到試算表，需要先在主控試算表（Jenny's Lemonhometools）的
「財務帳務設定」分頁，替台北／台中各新增一列：
  類型=工具包押金財報　地區=台北（或台中）　試算表ID=...　GID=...
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REPORT_TYPE,
    resolve_report_location,
)
from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

TW_TZ = ZoneInfo("Asia/Taipei")

DEPOSIT_AMOUNT_BY_AREA = {"台北": 2000, "台中": 1500}


def _deposit_amount(area: str) -> int:
    try:
        return DEPOSIT_AMOUNT_BY_AREA[area]
    except KeyError as exc:
        raise ValueError(f"不支援的工具包押金地區：{area}") from exc

EXECUTION_LOG_SHEET = "工具包押金執行記錄"


def _ensure_execution_log_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if EXECUTION_LOG_SHEET in titles:
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": EXECUTION_LOG_SHEET}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{EXECUTION_LOG_SHEET}'!A1:E1",
        valueInputOption="RAW",
        body={"values": [["時間", "地區", "步驟", "狀態", "訊息"]]},
    ).execute()


def _log_execution(area: str, step: str, status: str, message: str) -> None:
    """打卡記錄：寫進 Jenny's Lemonhometools 的「工具包押金執行記錄」分頁。"""
    service = get_sheets_service()
    master_id = get_master_spreadsheet_id()
    _ensure_execution_log_sheet(service, master_id)

    now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    service.spreadsheets().values().append(
        spreadsheetId=master_id,
        range=f"'{EXECUTION_LOG_SHEET}'!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [[now_text, area, step, status, message]]},
    ).execute()

COL_A, COL_B, COL_C, COL_D = 1, 2, 3, 4
COL_F, COL_G = 6, 7
COL_J, COL_K, COL_L, COL_M, COL_N = 10, 11, 12, 13, 14
COL_U, COL_V, COL_W, COL_X, COL_Z = 21, 22, 23, 24, 26

_TYPE_TO_JKLM_COL = {"上課": COL_J, "退還": COL_K, "已退": COL_L, "不退": COL_M}
_NOTE_HEAD_RE = re.compile(r"^(上課|退還|已退|不退)\s*[:：]\s*(.+)")
# 日期欄位在不同欄位可能用「-」或「/」分隔（例如 C/D 欄的日期型別儲存格會顯示
# 為 2026-09-04，F/G 欄手動輸入的備註則是 2026/07/27已匯款），兩種都要能比對到。
_LEADING_DATE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(.*)$")
_DATE_ONLY_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
_CELL_REF_RE = re.compile(r"^([A-Z]*)(\d+)$")
_RANGE_SEP_RE = re.compile(r"[:\-]")
DEFAULT_NOTE_COLUMN = "U"


def _col_letter_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def _read_values(spreadsheet_id: str, title: str) -> list[list[object]]:
    """讀整張分頁（A:Z），日期欄以字串回傳（例如 2026/1/12），跟原表的規則一致。"""
    service = get_sheets_service()
    res = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A:Z",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    return res.get("values", [])


def _cell(row: list[object], col: int) -> object:
    idx = col - 1
    return row[idx] if idx < len(row) else ""


def _year_month(date_text: str) -> str:
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", date_text)
    if not match:
        return ""
    return f"{int(match.group(1)):04d}{int(match.group(2)):02d}"


def _date_year(date_text: str) -> str:
    match = re.match(r"^(\d{4})[-/]\d{1,2}[-/]\d{1,2}$", date_text)
    return match.group(1) if match else ""


def _aggregate(values: list[list[object]], type_: str, year_month: str) -> tuple[list[str], int]:
    """比照原 getAggregateData_：回傳 (姓名清單, 人數)。"""
    names: list[str] = []

    for row in values[1:]:
        name = str(_cell(row, COL_A) or "").strip()
        if not name:
            continue

        if type_ == "上課":
            start_text = str(_cell(row, COL_C) or "").strip()
            end_text = str(_cell(row, COL_D) or "").strip()
            if _DATE_ONLY_RE.match(start_text) and _DATE_ONLY_RE.match(end_text):
                if _year_month(start_text) == year_month:
                    names.append(name)
            continue

        for col in (COL_F, COL_G):
            text = str(_cell(row, col) or "").strip()
            if not text:
                continue

            if type_ == "退還" and _DATE_ONLY_RE.match(text):
                if _year_month(text) == year_month:
                    names.append(name)
                continue

            match = _LEADING_DATE_RE.match(text)
            if not match:
                continue
            note_part = match.group(4)
            date_year_month = f"{int(match.group(1)):04d}{int(match.group(2)):02d}"
            if date_year_month != year_month:
                continue
            if type_ == "已退" and "已匯款" in note_part:
                names.append(name)
            if type_ == "不退" and "不退" in note_part:
                names.append(name)

    if type_ in ("已退", "不退"):
        seen: set[str] = set()
        deduped = []
        for n in names:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        names = deduped

    return names, len(names)


def _last_non_empty_row(values: list[list[object]], col: int) -> int:
    for row_idx in range(len(values), 0, -1):
        row = values[row_idx - 1]
        if str(_cell(row, col) or "").strip():
            return row_idx
    return 1


def aggregate_month_to_uy(area: str, year_month: str) -> int:
    """統計上課/退還/已退/不退，追加寫入 U–X（Y 欄不動，沿用表內公式）。

    每新增一列都會同步在 Z 欄註記當天日期（台北時區），回傳新增列數。
    """
    _log_execution(area, "統計月份彙整", "開始", year_month)
    try:
        written = _aggregate_month_to_uy(area, year_month)
    except Exception as exc:
        _log_execution(area, "統計月份彙整", "失敗", f"{year_month}：{exc}")
        raise
    _log_execution(area, "統計月份彙整", "完成", f"{year_month}：新增 {written} 列")
    return written


def _aggregate_month_to_uy(area: str, year_month: str) -> int:
    spreadsheet_id, title = resolve_report_location(DEPOSIT_REPORT_TYPE, area)
    values = _read_values(spreadsheet_id, title)
    if not values:
        return 0

    types = ["上課", "退還", "已退", "不退"]
    results = [_aggregate(values, t, year_month) for t in types]
    deposit_amount = _deposit_amount(area)

    start_row = max(_last_non_empty_row(values, COL_U) + 1, 2)
    updated_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    data = []
    out_row = start_row
    written = 0

    for type_, (names, total) in zip(types, results):
        if total <= 0:
            continue
        amount = total * deposit_amount
        note = f"{type_}：{'/'.join(names)}＋共{total}人"
        period_text = f"{year_month}{type_}"
        income = amount if type_ == "上課" else 0
        expense = 0 if type_ == "上課" else amount
        data.append(
            {
                "range": f"'{title}'!U{out_row}:X{out_row}",
                "values": [[note, period_text, income, expense]],
            }
        )
        data.append(
            {
                "range": f"'{title}'!Z{out_row}",
                "values": [[updated_date]],
            }
        )
        out_row += 1
        written += 1

    if data:
        service = get_sheets_service()
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    return written


def _parse_note(note_text: str) -> tuple[str, list[str]] | None:
    match = _NOTE_HEAD_RE.match(note_text.strip())
    if not match:
        return None
    type_, content = match.group(1), match.group(2)
    names_part = content.split("＋")[0].strip()
    names = [
        n.strip()
        for n in re.split(r"[/／、，,\s]+", names_part)
        if n.strip() and not re.fullmatch(r"共\d+人", n.strip()) and not n.strip().isdigit()
    ]
    if not names:
        return None
    return type_, names


def mark_from_notes(area: str, range_input: str) -> dict[str, object]:
    """依備註欄位批次打勾 J–M（比照原 processCustomNotesRange，每次執行會先清空 J:M）。

    範圍可以寫完整儲存格範圍（例如 U18:U23），也可以只給列號（例如 18-23
    或 18:23）——只給列號時預設抓 U 欄（統計月份彙整寫入備註的欄位）。
    """
    _log_execution(area, "批次依備註打勾", "開始", range_input)
    try:
        result = _mark_from_notes(area, range_input)
    except Exception as exc:
        _log_execution(area, "批次依備註打勾", "失敗", f"{range_input}：{exc}")
        raise
    _log_execution(
        area,
        "批次依備註打勾",
        "完成",
        f"{range_input}：打勾 {result['marked']} 個，找不到姓名 {len(result.get('missing_names') or [])} 個",
    )
    return result


def _mark_from_notes(area: str, range_input: str) -> dict[str, object]:
    spreadsheet_id, title = resolve_report_location(DEPOSIT_REPORT_TYPE, area)
    range_input = range_input.strip().upper()
    parts = _RANGE_SEP_RE.split(range_input, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("請輸入正確的範圍格式，例如：U18:U23 或 18-23（不寫欄位代號預設抓 U 欄）")

    start_match = _CELL_REF_RE.match(parts[0].strip())
    end_match = _CELL_REF_RE.match(parts[1].strip())
    if not start_match or not end_match:
        raise ValueError("請輸入正確的範圍格式，例如：U18:U23 或 18-23（不寫欄位代號預設抓 U 欄）")

    start_col_letters = start_match.group(1) or DEFAULT_NOTE_COLUMN
    end_col_letters = end_match.group(1) or start_col_letters
    if start_col_letters != end_col_letters:
        raise ValueError("請輸入同一欄位的範圍，例如：U18:U23")

    note_col = _col_letter_to_index(start_col_letters)
    start_row = min(int(start_match.group(2)), int(end_match.group(2)))
    end_row = max(int(start_match.group(2)), int(end_match.group(2)))
    if start_row < 1:
        raise ValueError("起始行不能小於 1")

    values = _read_values(spreadsheet_id, title)
    last_row = len(values)
    if last_row < 2:
        return {"marked": 0, "valid_notes": 0, "missing_names": [], "processed_rows": 0}

    total_rows = last_row - 1

    name_to_row: dict[str, int] = {}
    for i in range(total_rows):
        row = values[i + 1] if i + 1 < len(values) else []
        name = str(_cell(row, COL_A) or "").strip()
        if name and name not in ("FALSE", "專員姓名"):
            name_to_row[name] = i + 2

    service = get_sheets_service()

    # 先清空 J:M（允許多次重複執行仍保持正確）
    clear_values = [[False, False, False, False] for _ in range(total_rows)]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!J2:M{last_row}",
        valueInputOption="RAW",
        body={"values": clear_values},
    ).execute()

    notes = []
    for r in range(start_row, end_row + 1):
        row = values[r - 1] if r - 1 < len(values) else []
        notes.append(str(_cell(row, note_col) or "").strip())

    marks: dict[int, dict[int, bool]] = {}
    valid_notes = 0
    total_marked = 0
    missing_names: list[str] = []

    for i, note in enumerate(notes):
        if not note:
            continue
        parsed = _parse_note(note)
        if not parsed:
            continue
        type_, names = parsed
        target_col = _TYPE_TO_JKLM_COL.get(type_)
        if not target_col:
            continue
        valid_notes += 1
        for name in names:
            target_row = name_to_row.get(name)
            if target_row:
                marks.setdefault(target_row, {})[target_col] = True
                total_marked += 1
            else:
                missing_names.append(f"{name}（在 {start_col_letters}{start_row + i}）")

    if marks:
        data = []
        for row_num, cols in marks.items():
            for col, value in cols.items():
                col_letter = chr(ord("A") + col - 1)
                data.append(
                    {
                        "range": f"'{title}'!{col_letter}{row_num}",
                        "values": [[value]],
                    }
                )
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()

    return {
        "marked": total_marked,
        "valid_notes": valid_notes,
        "missing_names": missing_names,
        "processed_rows": len(notes),
    }


def flag_discrepancies(area: str) -> dict[str, int]:
    """比對 J–M 與 B 欄，僅檢查「今年 + 已上課（J已勾）」的列，異常在 N 欄標記 X。"""
    _log_execution(area, "比對異常標記", "開始", "")
    try:
        result = _flag_discrepancies(area)
    except Exception as exc:
        _log_execution(area, "比對異常標記", "失敗", str(exc))
        raise
    _log_execution(
        area, "比對異常標記", "完成",
        f"檢查 {result['checked']} 人，標記異常 {result['flagged']} 筆",
    )
    return result


def _flag_discrepancies(area: str) -> dict[str, int]:
    spreadsheet_id, title = resolve_report_location(DEPOSIT_REPORT_TYPE, area)
    values = _read_values(spreadsheet_id, title)
    last_row = len(values)
    if last_row < 2:
        return {"flagged": 0, "checked": 0}

    year = datetime.now(TW_TZ).year
    deposit_amount = _deposit_amount(area)
    total_rows = last_row - 1
    n_values: list[list[str]] = []
    flagged = 0
    checked = 0

    for i in range(total_rows):
        row = values[i + 1] if i + 1 < len(values) else []
        b_val = _cell(row, COL_B)
        try:
            b_num = float(b_val) if b_val != "" else 0.0
        except (TypeError, ValueError):
            b_num = 0.0
        c_text = str(_cell(row, COL_C) or "").strip()
        j = _cell(row, COL_J) is True
        k = _cell(row, COL_K) is True
        l = _cell(row, COL_L) is True
        m = _cell(row, COL_M) is True

        is_this_year = _date_year(c_text) == str(year)

        mark = ""
        if is_this_year and j:
            checked += 1
            expected = 0 if (k or l or m) else deposit_amount
            if b_num != expected:
                mark = "X"
                flagged += 1

        n_values.append([mark])

    service = get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!N2:N{last_row}",
        valueInputOption="RAW",
        body={"values": n_values},
    ).execute()

    return {"flagged": flagged, "checked": checked}
