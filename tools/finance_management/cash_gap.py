"""All財報現金缺口：試算各地區的富邦餘額／元大餘額／內勤薪資／專員承攬費／
行銷費用（廣告／服務）／總營收／2%／營業稅／藍新發票金額，整批寫進
「現金缺口試算」工作表。

比照財報既有欄位配置：
  B=2  帳務日　G=7  富邦更新餘額　H=8  元大更新餘額
  L=12 富邦更新分類標籤　M=13 富邦更新該列金額
  M=13 元大更新分類標籤　N=14 元大更新該列金額
  （元大更新原生欄位比富邦更新多一欄，所以分類標籤／金額欄位對應往後移一欄）

各項目：
  富邦餘額／元大餘額＝各自報表當月最後一筆（帳務日 <= 當月最後一天）的餘額欄
  內勤薪資＝富邦更新 L欄 或 元大更新 M欄，文字包含「{年月}-內勤薪資」的列，
        兩邊分別加總金額欄後相加
  專員承攬費＝富邦更新 L欄 或 元大更新 M欄，文字同時包含「{年月}」與
        「專員承攬費-2」（不要求相鄰）的列，兩邊分別加總金額欄後相加。
        專員承攬費每月有兩期：-1（當月20日左右）、-2（次月10日左右），
        這裡只抓「-2」那期，年月本身不做偏移
  行銷費用(廣告)／行銷費用(服務)＝「財報設定」工作表各地區列的
        行銷ID（G欄）及行銷gid（H欄）所指定分頁第5列／第8列；欄位依月份與
        地區位移：7月從 AS 欄起，
        每月位移 7 欄（同一月的區塊依序是台北／桃園／新竹／台中／家電／
        高雄／總計）
  總營收＝財務分頁「{年月}預估」欄第3列
  2%＝總營收 × 2%
  營業稅＝總營收 × 5%
  年終費用＝財務分頁「{年月}預估」欄第97列 × 月份數
        （例如期別 202607，欄位取 7月那欄，月份數是 7）
  春酒費用＝財務分頁「{年月}預估」欄第135列 × 月份數，算法同上
  藍新發票金額＝主控試算表（Jenny's Lemonhometools，固定分頁 GID＝
        327631316，欄位為 A區域／B月份YYYYMM／C發票日期／D發票金額／
        E發票號碼／F發票狀態／G更新時間）裡，A欄＝地區、B欄＝月份都符合
        的列，取 D欄，若有多筆則加總

「其他地區以此類推」：同一組公式，只是換成該地區自己財報的「富邦更新」／
「元大更新」／「財務」分頁（走 statement_registry 的登記表查地區）。
"""

from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import (
    resolve_marketing_expense_location,
    resolve_statement_location,
)

CASH_GAP_SHEET_NAME = "現金缺口試算"
CASH_GAP_AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
CASH_GAP_ROWS = [
    ("富邦餘額", "富邦餘額"),
    ("元大餘額", "元大餘額"),
    ("內勤薪資", "內勤薪資"),
    ("專員承攬費", "專員承攬費"),
    ("行銷費用(廣告)", "行銷費用(廣告)"),
    ("行銷費用(服務)", "行銷費用(服務)"),
    ("總營收", "總營收"),
    ("2%", "2%"),
    ("營業稅", "營業稅"),
    ("年終費用", "年終費用"),
    ("春酒費用", "春酒費用"),
    ("藍新發票金額", "藍新發票金額"),
]

COL_B, COL_G, COL_H = 2, 7, 8
COL_FUBON_LABEL, COL_FUBON_AMOUNT = 12, 13  # 富邦更新：L 標籤／M 金額
COL_YUANTA_LABEL, COL_YUANTA_AMOUNT = 13, 14  # 元大更新：M 標籤／N 金額（假設，見檔頭說明）

MARKETING_ROW_AD = 5
MARKETING_ROW_SERVICE = 8
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
FINANCE_REVENUE_ROW = 3
FINANCE_YEAR_END_BONUS_ROW = 97
FINANCE_SPRING_PARTY_ROW = 135

NEWEBPAY_INVOICE_SHEET = "藍新發票總表"
NEWEBPAY_INVOICE_AMOUNT_COL = 4  # D


def taipei_year() -> int:
    return datetime.now(ZoneInfo("Asia/Taipei")).year


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


def _read_values(spreadsheet_id: str, title: str, service=None) -> list[list[object]]:
    service = service or get_sheets_service()
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


def is_permission_error(exc: Exception) -> bool:
    text = str(exc)
    return "403" in text and ("permission" in text.lower() or "PERMISSION_DENIED" in text)


_REPORT_CACHE: dict[tuple[str, str, int | None], tuple[float, list[list[object]]]] = {}
REPORT_CACHE_SECONDS = 300


def _report_values(area: str, report: str, year: int | None = None) -> list[list[object]]:
    """讀各區財報（同一次執行 5 分鐘內同一個分頁只讀一次：富邦更新每區要用好幾次，重複讀會撞 429）。
    year＝財報年度，沒指定＝今年。"""
    key = (area, report, year)
    cached = _REPORT_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < REPORT_CACHE_SECONDS:
        return cached[1]
    values = _report_values_now(area, report, year)
    _REPORT_CACHE[key] = (time.monotonic(), values)
    return values


def _report_values_now(area: str, report: str, year: int | None = None) -> list[list[object]]:
    """讀各區財報。服務帳號沒有權限（403，例如桃園／新竹／高雄財報沒分享給服務帳號）時，
    改用 Jenny 本人的 OAuth 再讀一次。"""
    try:
        spreadsheet_id, title = resolve_statement_location(area, report, year=year)
        return _read_values(spreadsheet_id, title)
    except Exception as exc:
        if not is_permission_error(exc):
            raise
        from services.google_auth import get_sheets_service as oauth_sheets_service

        oauth = oauth_sheets_service()
        spreadsheet_id, title = resolve_statement_location(area, report, service=oauth, year=year)
        return _read_values(spreadsheet_id, title, service=oauth)


def _month_end_balance_from_values(values: list[list[object]], balance_col: int, as_of: date) -> float:
    last_value = 0.0
    for row in values[1:]:
        row_date = _parse_date(_cell(row, COL_B))
        if row_date is None or row_date > as_of:
            continue
        last_value = _to_number(_cell(row, balance_col))
    return last_value


def fubon_balance(area: str, as_of: date, values: list[list[object]] | None = None) -> float:
    """富邦餘額：富邦更新當月最後一筆的 G 欄餘額。"""
    values = values if values is not None else _report_values(area, "富邦更新")
    return _month_end_balance_from_values(values, COL_G, as_of)


def yuanta_balance(area: str, as_of: date, values: list[list[object]] | None = None) -> float:
    """元大餘額：元大更新當月最後一筆的 H 欄餘額。"""
    values = values if values is not None else _report_values(area, "元大更新")
    return _month_end_balance_from_values(values, COL_H, as_of)


def _contains_all_sum_from_values(
    values: list[list[object]], label_col: int, amount_col: int, required: list[str]
) -> float:
    total = 0.0
    for row in values[1:]:
        text = str(_cell(row, label_col) or "")
        if all(fragment in text for fragment in required):
            total += _to_number(_cell(row, amount_col))
    return total


def _dual_source_label_sum(
    required: list[str],
    fubon_values: list[list[object]],
    yuanta_values: list[list[object]],
) -> float:
    """富邦更新 L欄 或 元大更新 M欄，文字同時包含 required 裡每個片段的列，
    兩邊分別加總後相加。"""
    return _contains_all_sum_from_values(
        fubon_values, COL_FUBON_LABEL, COL_FUBON_AMOUNT, required
    ) + _contains_all_sum_from_values(
        yuanta_values, COL_YUANTA_LABEL, COL_YUANTA_AMOUNT, required
    )


def internal_staff_salary(
    area: str, as_of: date,
    fubon_values: list[list[object]] | None = None,
    yuanta_values: list[list[object]] | None = None,
) -> float:
    """內勤薪資：富邦更新 L欄 或 元大更新 M欄，包含「{年月}-內勤薪資」的列加總。"""
    fubon_values = fubon_values if fubon_values is not None else _report_values(area, "富邦更新")
    yuanta_values = yuanta_values if yuanta_values is not None else _report_values(area, "元大更新")
    fragment = f"{as_of.strftime('%Y.%m')}-內勤薪資"
    return _dual_source_label_sum([fragment], fubon_values, yuanta_values)


def specialist_contract_fee(
    area: str, as_of: date,
    fubon_values: list[list[object]] | None = None,
    yuanta_values: list[list[object]] | None = None,
) -> float:
    """專員承攬費：富邦更新 L欄 或 元大更新 M欄，同時包含「{年月}」與「專員承攬費-2」
    的列加總（不要求兩段文字相鄰，因為有時會多出領現等其他註記文字）。

    專員承攬費每月有兩期：專員承攬費-1（當月20日左右）、專員承攬費-2（次月10日
    左右）；這裡只抓「-2」那期，年月不做偏移，就是目標月份本身。
    """
    fubon_values = fubon_values if fubon_values is not None else _report_values(area, "富邦更新")
    yuanta_values = yuanta_values if yuanta_values is not None else _report_values(area, "元大更新")
    year_month = as_of.strftime("%Y.%m")
    return _dual_source_label_sum([year_month, "專員承攬費-2"], fubon_values, yuanta_values)


def marketing_expense_row(area: str, month: int, row: int, year: int | None = None) -> float:
    """行銷費用總管理試算表，指定月份／地區／列數那一格（year＝「{YYYY}總表」年度，沒指定＝今年）。"""
    if area not in MARKETING_REGION_OFFSETS:
        raise ValueError(f"行銷費用檔沒有「{area}」這個地區欄位")
    spreadsheet_id, title = resolve_marketing_expense_location(area, year=year)
    service = get_sheets_service()
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
            range=f"'{title}'!{column_letter}{row}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    values = res.get("values", [])
    return _to_number(values[0][0]) if values and values[0] else 0.0


def _finance_column_value(area: str, month: int, row: int, service=None, year: int | None = None) -> float:
    """財務分頁「{年月}預估」欄，指定列數的值（year＝財報年度，沒指定＝今年）。服務帳號沒權限時改用 Jenny OAuth。"""
    if service is None:
        try:
            return _finance_column_value(area, month, row, service=get_sheets_service(), year=year)
        except Exception as exc:
            if not is_permission_error(exc):
                raise
            from services.google_auth import get_sheets_service as oauth_sheets_service

            return _finance_column_value(area, month, row, service=oauth_sheets_service(), year=year)
    spreadsheet_id, title = resolve_statement_location(area, "財務", service=service, year=year)
    column_index = FINANCE_BASE_COLUMN + (month - 1) * FINANCE_MONTH_STEP_COLUMNS
    column_letter = _column_letter(column_index)
    res = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!{column_letter}{row}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    values = res.get("values", [])
    return _to_number(values[0][0]) if values and values[0] else 0.0


def finance_revenue(area: str, month: int, year: int | None = None) -> float:
    """總營收：財務分頁「{年月}預估」欄第3列。"""
    return _finance_column_value(area, month, FINANCE_REVENUE_ROW, year=year)


def newebpay_invoice_amount(area: str, as_of: date) -> float:
    """藍新發票金額：主控試算表固定分頁（GID=327631316）裡，A欄＝地區、B欄＝
    月份（YYYYMM）都符合的列，取 D欄（發票金額），若有多筆則加總。高雄不用算（2026-11 Jenny），一律 0。"""
    from tools.newebpay.month_close import NO_INVOICE_AREAS

    if area in NO_INVOICE_AREAS:
        return 0.0
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    title = NEWEBPAY_INVOICE_SHEET
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:Z",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    values = res.get("values", [])
    year_month = as_of.strftime("%Y%m")
    total = 0.0
    for row in values[1:]:
        if str(_cell(row, 1) or "").strip() != area:
            continue
        if str(_cell(row, 2) or "").strip() != year_month:
            continue
        total += _to_number(_cell(row, NEWEBPAY_INVOICE_AMOUNT_COL))
    return total


def is_missing_account(exc: Exception, report: str) -> bool:
    """財報沒有這個銀行的分頁＝這區沒有這個帳戶（高雄沒有富邦）。"""
    return f"找不到「{report}」分頁" in str(exc)


def compute_cash_gap(area: str, as_of: date) -> dict[str, float | str]:
    """回傳現金缺口試算各項目的值，試算但不寫回試算表。

    as_of 是要試算的月份最後一天（例如 2026/7/31 傳 date(2026, 7, 31)）。

    每一項獨立算、獨立擋錯：其中一項出錯（例如某份外部試算表還沒授權給
    服務帳號）不會讓其他項也算不出來，該項改填錯誤訊息，方便一眼看出
    是哪一項、哪個地區出問題。
    """
    month = as_of.month
    # 讀試算月份所屬年度的財報與「{YYYY}總表」：1 月試算 12 月仍讀 2026 年檔。
    current_year = taipei_year()
    year = as_of.year if as_of.year < current_year else None

    # 富邦更新／元大更新各只讀一次、重複使用，避免同一份財報短時間內被讀
    # 好幾次，觸發 Sheets API 的每分鐘讀取配額（跑「全區」很容易撞到）。
    def _try_fetch(report: str, report_year: int | None = year):
        try:
            return _report_values(area, report, report_year), None
        except Exception as exc:
            if is_missing_account(exc, report):
                # 這區沒有這個銀行帳戶（例如高雄沒有富邦，財報沒有「富邦更新」分頁）：當成 0，不算錯誤
                return [], None
            return None, exc

    fubon_values, fubon_error = _try_fetch("富邦更新")
    yuanta_values, yuanta_error = _try_fetch("元大更新")
    # 餘額只看試算月份那一年的財報；內勤薪資、專員承攬費-2 在次月才匯款，
    # 試算 12 月時再加上隔年財報裡標「{YYYY}.12」的列。
    fubon_label_values, yuanta_label_values = fubon_values, yuanta_values
    if year is not None and fubon_error is None and yuanta_error is None:
        next_year = as_of.year + 1 if as_of.year + 1 < current_year else None
        next_fubon, next_fubon_error = _try_fetch("富邦更新", next_year)
        next_yuanta, next_yuanta_error = _try_fetch("元大更新", next_year)
        fubon_error = fubon_error or next_fubon_error
        yuanta_error = yuanta_error or next_yuanta_error
        if fubon_error is None and yuanta_error is None:
            fubon_label_values = fubon_values + next_fubon[1:]
            yuanta_label_values = yuanta_values + next_yuanta[1:]

    def _need1(values, error, compute):
        if error is not None:
            raise error
        return compute(values)

    def _need2(compute):
        if fubon_error is not None:
            raise fubon_error
        if yuanta_error is not None:
            raise yuanta_error
        return compute(fubon_values, yuanta_values)

    def _revenue_based(multiplier):
        return lambda: finance_revenue(area, month, year) * multiplier

    computations = {
        "富邦餘額": lambda: _need1(fubon_values, fubon_error, lambda v: fubon_balance(area, as_of, v)),
        "元大餘額": lambda: _need1(yuanta_values, yuanta_error, lambda v: yuanta_balance(area, as_of, v)),
        "內勤薪資": lambda: _need2(lambda fv, yv: internal_staff_salary(
            area, as_of, fubon_label_values, yuanta_label_values)),
        "專員承攬費": lambda: _need2(lambda fv, yv: specialist_contract_fee(
            area, as_of, fubon_label_values, yuanta_label_values)),
        "行銷費用(廣告)": lambda: marketing_expense_row(area, month, MARKETING_ROW_AD, year),
        "行銷費用(服務)": lambda: marketing_expense_row(area, month, MARKETING_ROW_SERVICE, year),
        "總營收": lambda: finance_revenue(area, month, year),
        "2%": _revenue_based(0.02),
        "營業稅": _revenue_based(0.05),
        "年終費用": lambda: _finance_column_value(area, month, FINANCE_YEAR_END_BONUS_ROW, year=year) * month,
        "春酒費用": lambda: _finance_column_value(area, month, FINANCE_SPRING_PARTY_ROW, year=year) * month,
        "藍新發票金額": lambda: newebpay_invoice_amount(area, as_of),
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


def keep_previous_values(service, spreadsheet_id: str, results: dict, areas: list[str], date_text: str) -> list[str]:
    """這次讀取失敗（429 之類）的格子，表上如果已經有同一天算好的數字，就沿用，不要蓋成錯誤。回傳沿用的項目。"""
    try:
        old = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{CASH_GAP_SHEET_NAME}'!A1:G{len(CASH_GAP_ROWS) + 2}",
            valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING").execute().get("values", [])
    except Exception:
        return []
    header = [str(v).strip() for v in (old[0] if old else [])]
    dates = old[1] if len(old) > 1 else []
    by_label = {str(r[0]).strip(): r for r in old[2:] if r}
    kept = []
    for area in areas:
        if area not in header:
            continue
        col = header.index(area)
        if col >= len(dates) or str(dates[col]).strip().replace("-", "/") != date_text:
            continue
        for label, key in CASH_GAP_ROWS:
            row = by_label.get(label, [])
            previous = row[col] if col < len(row) else ""
            if isinstance(results[area].get(key), str) and isinstance(previous, (int, float)):
                results[area][key] = previous
                kept.append(f"{area} {label}")
    return kept


def write_cash_gap_sheet(
    as_of: date, areas: list[str] | None = None, on_progress=None
) -> dict[str, dict[str, float]]:
    """試算各地區現金缺口，整批寫進主控試算表（Jenny's Lemonhometools）裡獨立的
    「現金缺口試算」工作表（不動各地區財報原本的 BF 欄，也不需要在「財報設定」
    分頁另外設定試算表ID）。

    on_progress(msg, level) 可選，每算完一個地區就回報一次，讓呼叫端（例如
    Streamlit 執行日誌）即時顯示進度，不用等全部地區跑完才看到結果。
    """
    areas = areas or CASH_GAP_AREAS
    _REPORT_CACHE.clear()  # 每次試算重新讀最新的財報
    try:  # 429（每分鐘讀取上限）自動等一下再重試
        from services.google_api_retry import install_googleapiclient_retry

        install_googleapiclient_retry()
    except Exception:
        pass
    log_execution("All財報現金缺口", "／".join(areas), "開始", f"期別截至 {as_of}")
    results = {}
    for i, area in enumerate(areas):
        if i > 0:
            time.sleep(2)  # 地區之間留點間隔，避免瞬間打太多 Sheets API 請求觸發配額限制
        if on_progress:
            on_progress(f"{area}：試算中...", "info")
        results[area] = compute_cash_gap(area, as_of)
        errors = [k for k, v in results[area].items() if isinstance(v, str)]
        if errors:
            message = f"{area}：完成，但 {'、'.join(errors)} 出錯"
            if on_progress:
                on_progress(message, "warning")
        else:
            message = f"{area}：試算完成"
            if on_progress:
                on_progress(message, "success")
        log_execution("All財報現金缺口", area, "完成" if not errors else "部分失敗", message)

    spreadsheet_id = get_master_spreadsheet_id()
    service = get_sheets_service()
    _ensure_cash_gap_sheet(service, spreadsheet_id)

    date_text = as_of.strftime("%Y/%m/%d")
    kept = keep_previous_values(service, spreadsheet_id, results, areas, date_text)
    if kept and on_progress:
        on_progress("這次讀取失敗、沿用表上同一天的數字：" + "、".join(kept), "warning")
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
