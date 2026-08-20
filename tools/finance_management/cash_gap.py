"""All財報現金缺口：試算各地區的富邦餘額／元大餘額／內勤薪資／專員承攬費／
行銷費用／2%支出，整批寫進「現金缺口試算」工作表。

比照財報既有欄位配置（見 statement_update.py）：
  B=2  帳務日　G=7  富邦更新餘額　H=8  元大更新餘額　L=12  摘要／分類標籤　M=13  該列累計金額

六個項目：
  富邦餘額＝富邦更新當月最後一筆（帳務日 <= 當月最後一天）G欄餘額
  元大餘額＝元大更新當月最後一筆（帳務日 <= 當月最後一天）H欄餘額
  內勤薪資＝富邦更新中，帳務日 < 次月5日 且 L欄＝「{年月}-內勤薪資」的那一列 M欄
  專員承攬費＝富邦更新中，帳務日 < 次月10日 且 L欄＝「{年月}-專員薪資」或
        「{年月}-專員承攬費」的列，加總 M欄
  行銷費用＝行銷費用總管理試算表（固定分頁 GID＝228482464）第9列，欄位依月份與
        地區位移：7月從 AS 欄起，每月位移 7 欄（同一月的區塊依序是
        台北／桃園／新竹／台中／家電／高雄／總計）
  2%支出＝財務分頁 O2 起算：單月只取當月欄；雙月要加回前一月欄
        （1月對應 C 欄，每月位移 2 欄，7月＝O，8月＝Q ...）

「其他地區以此類推」：同一組公式，只是換成該地區自己財報的「富邦更新」／
「元大更新」／「財務」分頁（走 statement_registry 的登記表查地區）。
"""

from __future__ import annotations

from datetime import date

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.statement_registry import (
    marketing_expense_spreadsheet_id,
    resolve_statement_location,
    sheet_title_for_gid,
)

CASH_GAP_SHEET_NAME = "現金缺口試算"
CASH_GAP_AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
CASH_GAP_ROWS = [
    ("富邦餘額", "富邦餘額"),
    ("元大餘額", "元大餘額"),
    ("內勤薪資", "內勤薪資"),
    ("專員承攬費", "專員承攬費"),
    ("行銷費用", "行銷費用"),
    ("2%支出", "2%支出"),
]

COL_B, COL_G, COL_H, COL_L, COL_M = 2, 7, 8, 12, 13

MARKETING_REPORT_GID = "228482464"
MARKETING_ROW = 9
MARKETING_BASE_COLUMN = 45  # AS，對應 7月
MARKETING_MONTH_STEP_COLUMNS = 7
MARKETING_BASE_MONTH = 7
MARKETING_REGION_OFFSETS = {
    "台北": 0,
    "桃園": 1,
    "新竹": 2,
    "台中": 3,
    "家電": 4,
    "高雄": 5,
    "總計": 6,
}

FINANCE_BASE_COLUMN = 3  # C，對應 1月
FINANCE_MONTH_STEP_COLUMNS = 2
FINANCE_ROW = 2


def _cell(row: list[object], col: int) -> object:
    idx = col - 1
    return row[idx] if idx < len(row) else ""


def _to_number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip().split(" ", 1)[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _read_values(spreadsheet_id: str, title: str) -> list[list[object]]:
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


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _month_end_balance(area: str, report: str, balance_col: int, as_of: date) -> float:
    """BF14 用：report 分頁裡帳務日 <= as_of 的最後一列，取 balance_col 欄的值。"""
    spreadsheet_id, title = resolve_statement_location(area, report)
    values = _read_values(spreadsheet_id, title)
    last_value = 0.0
    for row in values[1:]:
        row_date = _parse_date(_cell(row, COL_B))
        if row_date is None or row_date > as_of:
            continue
        last_value = _to_number(_cell(row, balance_col))
    return last_value


def fubon_balance(area: str, as_of: date) -> float:
    """富邦餘額：富邦更新當月最後一筆的 G 欄餘額。"""
    return _month_end_balance(area, "富邦更新", COL_G, as_of)


def yuanta_balance(area: str, as_of: date) -> float:
    """元大餘額：元大更新當月最後一筆的 H 欄餘額。"""
    return _month_end_balance(area, "元大更新", COL_H, as_of)


def _label_amount_before(area: str, labels: list[str], before: date) -> float:
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
    values = _read_values(spreadsheet_id, title)
    total = 0.0
    for row in values[1:]:
        row_date = _parse_date(_cell(row, COL_B))
        if row_date is None or row_date >= before:
            continue
        if str(_cell(row, COL_L) or "").strip() not in labels:
            continue
        total += _to_number(_cell(row, COL_M))
    return total


def internal_staff_salary(area: str, year_month: str, before: date) -> float:
    """BF21：富邦更新裡「{年月}-內勤薪資」、帳務日 < before 的那一列 M欄。"""
    return _label_amount_before(area, [f"{year_month}-內勤薪資"], before)


def specialist_salary(area: str, year_month: str, before: date) -> float:
    """專員承攬費：富邦更新裡「{年月}-專員薪資」或「{年月}-專員承攬費」、
    帳務日 < before 的列，加總 M欄。"""
    labels = [f"{year_month}-專員薪資", f"{year_month}-專員承攬費"]
    return _label_amount_before(area, labels, before)


def marketing_expense(area: str, month: int) -> float:
    """行銷費用：行銷費用總管理試算表，指定月份／地區那一格（固定第9列）。"""
    if area not in MARKETING_REGION_OFFSETS:
        raise ValueError(f"行銷費用檔沒有「{area}」這個地區欄位")
    spreadsheet_id = marketing_expense_spreadsheet_id()
    service = get_sheets_service()
    title = sheet_title_for_gid(service, spreadsheet_id, MARKETING_REPORT_GID)
    column_index = (
        MARKETING_BASE_COLUMN
        + (month - MARKETING_BASE_MONTH) * MARKETING_MONTH_STEP_COLUMNS
        + MARKETING_REGION_OFFSETS[area]
    )
    column_letter = _column_letter(column_index)
    res = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!{column_letter}{MARKETING_ROW}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    values = res.get("values", [])
    return _to_number(values[0][0]) if values and values[0] else 0.0


def _finance_month_column_value(area: str, month: int) -> float:
    spreadsheet_id, title = resolve_statement_location(area, "財務")
    service = get_sheets_service()
    column_index = FINANCE_BASE_COLUMN + (month - 1) * FINANCE_MONTH_STEP_COLUMNS
    column_letter = _column_letter(column_index)
    res = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!{column_letter}{FINANCE_ROW}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    values = res.get("values", [])
    return _to_number(values[0][0]) if values and values[0] else 0.0


def finance_bimonthly_value(area: str, month: int) -> float:
    """BF24：財務分頁；單月只取當月欄，雙月要加回前一月欄。"""
    value = _finance_month_column_value(area, month)
    if month % 2 == 0:
        value += _finance_month_column_value(area, month - 1)
    return value


def _next_month_cutoff(as_of: date, day: int) -> date:
    year = as_of.year + (1 if as_of.month == 12 else 0)
    month = 1 if as_of.month == 12 else as_of.month + 1
    return date(year, month, day)


def compute_cash_gap(area: str, as_of: date) -> dict[str, float | str]:
    """回傳 {'富邦餘額', '元大餘額', '內勤薪資', '專員承攬費', '行銷費用', '2%支出'}，
    試算但不寫回試算表。

    as_of 是要試算的月份最後一天（例如 2026/7/31 傳 date(2026, 7, 31)）。

    每一項獨立算、獨立擋錯：其中一項出錯（例如某份外部試算表還沒授權給
    服務帳號）不會讓其他項也算不出來，該項改填錯誤訊息，方便一眼看出
    是哪一項、哪個地區出問題。
    """
    year_month = as_of.strftime("%Y.%m")
    computations = {
        "富邦餘額": lambda: fubon_balance(area, as_of),
        "元大餘額": lambda: yuanta_balance(area, as_of),
        "內勤薪資": lambda: internal_staff_salary(area, year_month, _next_month_cutoff(as_of, 5)),
        "專員承攬費": lambda: specialist_salary(area, year_month, _next_month_cutoff(as_of, 10)),
        "行銷費用": lambda: marketing_expense(area, as_of.month),
        "2%支出": lambda: finance_bimonthly_value(area, as_of.month),
    }
    result: dict[str, float | str] = {}
    for key, compute in computations.items():
        try:
            result[key] = compute()
        except Exception as exc:
            result[key] = f"錯誤：{exc}"
    return result


def _ensure_cash_gap_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if CASH_GAP_SHEET_NAME in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": CASH_GAP_SHEET_NAME}}}]},
    ).execute()


def write_cash_gap_sheet(
    as_of: date, areas: list[str] | None = None
) -> dict[str, dict[str, float]]:
    """試算各地區現金缺口，整批寫進主控試算表（Jenny's Lemonhometools）裡獨立的
    「現金缺口試算」工作表（不動各地區財報原本的 BF 欄，也不需要在「財報設定」
    分頁另外設定試算表ID）。"""
    areas = areas or CASH_GAP_AREAS
    results = {area: compute_cash_gap(area, as_of) for area in areas}

    spreadsheet_id = get_master_spreadsheet_id()
    service = get_sheets_service()
    _ensure_cash_gap_sheet(service, spreadsheet_id)

    date_text = as_of.strftime("%Y/%m/%d")
    rows = [
        [""] + areas + ["ALL"],
        ["日期"] + [date_text] * len(areas) + [date_text],
    ]
    for label, key in CASH_GAP_ROWS:
        values = [results[area][key] for area in areas]
        numeric_values = [v for v in values if isinstance(v, (int, float))]
        total = sum(numeric_values) if len(numeric_values) == len(values) else "—"
        rows.append([label, *values, total])

    last_col = _column_letter(len(areas) + 2)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{CASH_GAP_SHEET_NAME}'!A1:{last_col}{len(rows)}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()

    return results
