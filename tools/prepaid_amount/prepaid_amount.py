"""【檸檬後台】預收款金額。

檔案：tools/finance_management/prepaid_amount.py
版本：0822_v3
更新日期：2026-08-22

功能：
- 輸入年月（YYYYMM）＋地區，登入該地區檸檬後台，查詢「訂單管理」頁面的
  「訂單統計表」，取得三種欄位金額：
    1. 藍新ATM服務金額／信用卡服務金額／ATM服務金額
       （付款日期＝該月 1 日～月底、服務日期-起＝次月 1 日、付款狀態＝已付款、
        購買項目＝全部服務，依「付款方式」分別查 3 次，取「現金收入」表格
        「加總」列的「已付款金額」）
    2. 家電服務金額
       （同上日期／付款狀態，購買項目＝家電清潔，取該表格「加總」列的
        「已付款金額」；若該分類無「加總」列，取唯一資料列的金額）
    3. 水洗服務金額
       （同上日期／付款狀態，購買項目＝傢俱清潔，取法同家電服務金額）

- 查得的 5 個數字併入主控表（Jenny's Lemonhometools）的「現金缺口試算」
    工作表（tools/finance_management/cash_gap.py 寫的同一份分頁），接在
    現金缺口試算原本的 12 列下方（中間空 2 列）：
    欄位沿用現金缺口試算的地區欄（B～F＝台北／台中／桃園／新竹／高雄），
    列則是藍新ATM服務金額／信用卡服務金額／ATM服務金額／家電服務金額／
    水洗服務金額。寫入時只更新「執行地區」對應的那一欄，每次執行直接覆蓋
    （只保留最新一次查得的金額，不像舊版會依月份往右新增區塊保留歷史）。

- 執行記錄併入「財務工具執行記錄」分頁（跟財報富邦更新、現金缺口試算…等
    財務工具共用同一份，見 execution_log.py），不再自己開一份獨立的
    「預收款金額執行記錄」；沒有獨立的「月份」欄，改把期別併進訊息文字。
"""

from __future__ import annotations

import calendar
import re
import time

import requests
from bs4 import BeautifulSoup
from googleapiclient.errors import HttpError

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.cash_gap import CASH_GAP_AREAS, CASH_GAP_ROWS, CASH_GAP_SHEET_NAME
from tools.finance_management.execution_log import log_execution
from tools.scheduled_monthly.prepaid_report import LOGIN_URL, HEADERS, load_accounts, login, choose_keyword

PURCHASE_URL = "https://backend.lemonclean.com.tw/purchase"

SHEET_NAME = CASH_GAP_SHEET_NAME
CITIES = CASH_GAP_AREAS

ROW_LABELS = [
    "藍新ATM服務金額",
    "信用卡服務金額",
    "ATM服務金額",
    "家電服務金額",
    "水洗服務金額",
]

# 接在現金缺口試算原本的表頭（2列）＋ 12 個項目下方，中間空 2 列。
PREPAID_START_ROW = 2 + len(CASH_GAP_ROWS) + 2 + 1

# 對應後台「付款方式」下拉選單代碼
PAYWAY_CODE = {"藍新ATM服務金額": "5", "信用卡服務金額": "1", "ATM服務金額": "2"}
# 對應後台「購買項目」下拉選單代碼
BUY_CODE = {"家電服務金額": "2", "水洗服務金額": "3"}


def log(message: str) -> None:
    print(message, flush=True)


def _year_month_bounds(year_month: str) -> dict[str, str]:
    if not re.fullmatch(r"\d{6}", year_month):
        raise ValueError("請輸入 6 位數月份（YYYYMM），例如 202607")
    year, month = int(year_month[:4]), int(year_month[4:6])
    last_day = calendar.monthrange(year, month)[1]
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return {
        "paid_at_s": f"{year}-{month:02d}-01",
        "paid_at_e": f"{year}-{month:02d}-{last_day:02d}",
        "clean_date_s": f"{next_year}-{next_month:02d}-01",
        "date_label": f"{year}/{month}/{last_day}",
    }


def _fetch_purchase_html(session: requests.Session, *, payway: str = "", buy: str = "", keyword: str = "", bounds: dict[str, str]) -> str:
    params = {
        "keyword": keyword, "name": "", "phone": "", "orderNo": "",
        "date_s": "", "date_e": "",
        "clean_date_s": bounds["clean_date_s"], "clean_date_e": "",
        "paid_at_s": bounds["paid_at_s"], "paid_at_e": bounds["paid_at_e"],
        "refundDateS": "", "refundDateE": "",
        "buy": buy, "buy_item": "0", "area_id": "",
        "isCharge": "", "isRefund": "",
        "p_board": "on",
        "payway": payway,
        "purchase_status": "1",
        "progress_status": "", "invoiceStatus": "", "otherFee": "", "orderBy": "",
    }
    res = session.get(PURCHASE_URL, params=params, headers=HEADERS, timeout=60, allow_redirects=True)
    res.raise_for_status()
    return res.text


def _cell_text(cell) -> str:
    return cell.get_text(" ", strip=True)


def _extract_paid_total(html: str, *, corner_label: str | None) -> int:
    """從「訂單統計表」抓「已付款金額」加總。

    corner_label 指定時（例如「現金收入」），只吃表格左上角文字相符的表格；
    corner_label=None 時，只吃左上角是空白的表格（家電／水洗查詢時，後台只會
    顯示該分類專屬的表格，左上角欄位是空的）。
    """
    soup = BeautifulSoup(html, "html.parser")
    total = 0
    matched_any = False

    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        rows = [[_cell_text(c) for c in tr.find_all(["th", "td"])] for tr in trs]
        rows = [r for r in rows if any(x.strip() for x in r)]
        if not rows:
            continue

        header = rows[0]
        if "已付款金額" not in header:
            continue

        corner = header[0].strip() if header else ""
        if corner_label is not None:
            if corner != corner_label:
                continue
        else:
            if corner:
                continue

        matched_any = True
        paid_idx = header.index("已付款金額")
        data_rows = rows[1:]

        total_row = next((r for r in data_rows if r and r[0].strip() == "加總"), None)
        if total_row is not None:
            total += _safe_int(total_row[paid_idx] if len(total_row) > paid_idx else 0)
        elif len(data_rows) == 1:
            row = data_rows[0]
            total += _safe_int(row[paid_idx] if len(row) > paid_idx else 0)
        else:
            for row in data_rows:
                total += _safe_int(row[paid_idx] if len(row) > paid_idx else 0)

    if not matched_any:
        log(f"⚠️ 找不到符合的統計表格（corner_label={corner_label!r}）")
    return total


LOG_TOOL_NAME = "預收款金額"


def _log_execution(area: str, year_month: str, status: str, message: str) -> None:
    """記錄併入 Jenny's Lemonhometools 的「財務工具執行記錄」分頁（跟財報富邦更新、
    現金缺口試算…等財務工具共用同一份執行記錄，不再自己開一份「預收款金額執行
    記錄」；沒有獨立的「月份」欄，改把期別併進訊息文字）。log_execution() 本身
    失敗也吞掉，不讓查詢流程掛掉。"""
    log_execution(LOG_TOOL_NAME, area, status, f"期別 {year_month}｜{message}" if message else f"期別 {year_month}")


def _safe_int(value) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _query_all_amounts(session: requests.Session, bounds: dict[str, str], *, keyword: str = "", on_progress=None) -> dict[str, int]:
    amounts: dict[str, int] = {}

    def notify(msg: str, level: str = "info") -> None:
        log(msg)
        if on_progress:
            on_progress(msg, level)

    for label, code in PAYWAY_CODE.items():
        notify(f"查詢中：{label}")
        html = _fetch_purchase_html(session, payway=code, buy="", keyword=keyword, bounds=bounds)
        amounts[label] = _extract_paid_total(html, corner_label="現金收入")
        notify(f"{label}：{amounts[label]:,}", "success")

    for label, code in BUY_CODE.items():
        notify(f"查詢中：{label}")
        html = _fetch_purchase_html(session, payway="", buy=code, keyword=keyword, bounds=bounds)
        amounts[label] = _extract_paid_total(html, corner_label=None)
        notify(f"{label}：{amounts[label]:,}", "success")

    return amounts


# 高雄帳號的後台資料同時涵蓋高雄／台南，兩個關鍵字都要各查一次再加總；
# 新竹則要帶關鍵字「新竹」才篩得到正確資料（比照 prepaid_report.py 的規則）。
def _keywords_for_area(area: str) -> list[str]:
    if area == "高雄":
        return ["高雄", "台南"]
    if area == "新竹":
        return ["新竹"]
    return [""]


def _col_letter(index_1based: int) -> str:
    letters = ""
    n = index_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _execute_with_retry(request, *, max_retries: int = 6, base_delay: float = 5.0):
    """Sheets API 有每分鐘配額限制，429（配額超過）或 503（暫時過載）時
    用指數退避重試，避免跟其他工具同時打 API 就整批失敗。"""
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status not in (429, 503) or attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            log(f"⚠️ Sheets API 配額限制（{status}），{delay:.0f} 秒後重試（第 {attempt + 1}/{max_retries} 次）")
            time.sleep(delay)
    raise RuntimeError("重試次數用盡")


def _ensure_prepaid_rows(service, spreadsheet_id: str) -> None:
    """確保「現金缺口試算」分頁存在，且預收款 5 個項目的列標籤已經寫在 A 欄
    （PREPAID_START_ROW 起）。標籤內容固定，每次覆蓋也無妨。"""
    meta = _execute_with_retry(
        service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
    )
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if SHEET_NAME not in titles:
        _execute_with_retry(
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME}}}]},
            )
        )

    end_row = PREPAID_START_ROW + len(ROW_LABELS) - 1
    _execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A{PREPAID_START_ROW}:A{end_row}",
            valueInputOption="RAW",
            body={"values": [[label] for label in ROW_LABELS]},
        )
    )


def _write_area_column(service, spreadsheet_id: str, area: str, amounts: dict[str, int]) -> None:
    col_letter = _col_letter(2 + CITIES.index(area))  # 從 B 欄開始，跟現金缺口試算的地區欄對齊
    start_row = PREPAID_START_ROW
    end_row = PREPAID_START_ROW + len(ROW_LABELS) - 1
    values = [[amounts[label]] for label in ROW_LABELS]
    _execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!{col_letter}{start_row}:{col_letter}{end_row}",
            valueInputOption="RAW",
            body={"values": values},
        )
    )


def run(area: str, year_month: str, on_progress=None) -> str:
    if area not in CITIES:
        raise ValueError(f"請選擇正確地區：{'／'.join(CITIES)}")

    def notify(msg: str, level: str = "info") -> None:
        log(msg)
        if on_progress:
            on_progress(msg, level)

    _log_execution(area, year_month, "開始", "")

    try:
        bounds = _year_month_bounds(year_month)
        accounts = load_accounts()
        acc = accounts.get(area) or {}
        if not acc.get("email") or not acc.get("password"):
            raise RuntimeError(f"找不到 {area} 的後台帳號設定")

        notify(f"{area}：開始登入後台")
        session = requests.Session()
        login(session, acc["email"], acc["password"])
        notify(f"{area}：登入成功，開始查詢 {year_month}")

        keywords = _keywords_for_area(area)
        amounts = {label: 0 for label in ROW_LABELS}
        for keyword in keywords:
            tag = f"{area}（關鍵字：{keyword}）" if keyword else area
            part = _query_all_amounts(
                session, bounds, keyword=keyword,
                on_progress=lambda msg, level="info", tag=tag: notify(f"{tag}：{msg}", level),
            )
            for label in ROW_LABELS:
                amounts[label] += part[label]

        service = get_sheets_service()
        spreadsheet_id = get_master_spreadsheet_id()
        _ensure_prepaid_rows(service, spreadsheet_id)
        _write_area_column(service, spreadsheet_id, area, amounts)
        notify(f"{area}：已寫入「{SHEET_NAME}」分頁", "success")

        summary = "、".join(f"{label}={amounts[label]:,}" for label in ROW_LABELS)
        _log_execution(area, year_month, "完成", summary)
        return f"完成：{area} {year_month}（{bounds['date_label']}）已寫入「{SHEET_NAME}」分頁。{summary}"
    except Exception as exc:
        _log_execution(area, year_month, "失敗", str(exc))
        raise
