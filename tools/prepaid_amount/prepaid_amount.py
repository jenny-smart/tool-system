"""【檸檬後台】預收款金額。

檔案：tools/finance_management/prepaid_amount.py
版本：0820_v1
更新日期：2026-08-20

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

- 查得的 5 個數字寫入主控表（Jenny's Lemonhometools）新增分頁「預收款金額」：
    每個月份是一個「區塊」（5 欄，對應台北／台中／桃園／新竹／高雄），
    區塊第 1 列＝地區名稱、第 2 列＝該月月底日期（例如 2026/7/31）、
    第 3～7 列＝藍新ATM服務金額／信用卡服務金額／ATM服務金額／
    家電服務金額／水洗服務金額。
    寫入時只更新「執行地區」對應的那一欄；若找不到該月份的區塊就在最右邊
    新增一組區塊，找得到就直接覆蓋該欄位（不會動到其他地區的欄位）。
"""

from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from googleapiclient.errors import HttpError

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.scheduled_monthly.prepaid_report import LOGIN_URL, HEADERS, load_accounts, login, choose_keyword

TZ = timezone(timedelta(hours=8))

PURCHASE_URL = "https://backend.lemonclean.com.tw/purchase"

SHEET_NAME = "預收款金額"
CITIES = ["台北", "台中", "桃園", "新竹", "高雄"]

ROW_LABELS = [
    "藍新ATM服務金額",
    "信用卡服務金額",
    "ATM服務金額",
    "家電服務金額",
    "水洗服務金額",
]

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


def _find_or_create_block(service, spreadsheet_id: str, date_label: str) -> int:
    """回傳該月份區塊第一欄（台北欄）的欄位編號（1-based）。找不到就新增區塊。"""
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
        _execute_with_retry(
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{SHEET_NAME}'!A1:A7",
                valueInputOption="RAW",
                body={"values": [[""], [""], *[[label] for label in ROW_LABELS]]},
            )
        )

    res = _execute_with_retry(
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!B1:ZZ2",
        )
    )
    values = res.get("values", [])
    row1 = values[0] if len(values) > 0 else []
    row2 = values[1] if len(values) > 1 else []

    used_width = max(len(row1), len(row2))  # 已使用的欄數（從 B 欄開始算）
    block_count = -(-used_width // len(CITIES)) if used_width else 0  # 無條件進位

    block_start = 2  # 欄位編號（1-based），從 B 欄（=2）開始
    for block_no in range(block_count):
        idx = block_no * len(CITIES)
        date_here = row2[idx] if idx < len(row2) else ""
        if str(date_here).strip() == date_label:
            return block_start + block_no * len(CITIES)

    # 找不到，新增一個區塊（接在最後一個已使用區塊右邊）
    new_block_start = block_start + block_count * len(CITIES)

    start_letter = _col_letter(new_block_start)
    end_letter = _col_letter(new_block_start + len(CITIES) - 1)
    _execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!{start_letter}1:{end_letter}2",
            valueInputOption="RAW",
            body={"values": [CITIES, [date_label] * len(CITIES)]},
        )
    )
    return new_block_start


def _write_area_column(service, spreadsheet_id: str, block_start: int, area: str, amounts: dict[str, int]) -> None:
    col_index = block_start + CITIES.index(area)
    col_letter = _col_letter(col_index)
    values = [[amounts[label]] for label in ROW_LABELS]
    _execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!{col_letter}3:{col_letter}7",
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
    block_start = _find_or_create_block(service, spreadsheet_id, bounds["date_label"])
    _write_area_column(service, spreadsheet_id, block_start, area, amounts)
    notify(f"{area}：已寫入「{SHEET_NAME}」分頁", "success")

    summary = "、".join(f"{label}={amounts[label]:,}" for label in ROW_LABELS)
    return f"完成：{area} {year_month}（{bounds['date_label']}）已寫入「{SHEET_NAME}」分頁。{summary}"
