"""藍新帳務查詢（帳務中心 → 帳戶明細查詢）：取查詢結果第一列（最新一筆）的處理日期與「本次剩餘金額」，
寫進主控表「現金缺口試算」的「藍新帳務日期」「藍新帳務金額」兩列（各地區一欄），之後隨現金缺口貼到 review。

月初跟藍新收退款／發票同一次登入做（tools.newebpay.month_close --part balance）；
Tools App【藍新金流】藍新帳務查詢 也可以單獨排入本機 Agent。高雄跟新竹同一個帳號，不另外算（2026-11 Jenny）。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

BALANCE_URL = "https://www.newebpay.com/payment/withdraw_and_appropriation/withdraw_balance"
DATE_LABEL = "藍新帳務日期"
AMOUNT_LABEL = "藍新帳務金額"
NO_BALANCE_AREAS = ("高雄",)
MONEY_HEADERS = ("前次剩餘金額", "本次金額", "手續費", "本次剩餘金額")
_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")


def parse_amount(text: str) -> int:
    digits = re.sub(r"[^0-9-]", "", text or "")
    if not digits or digits == "-":
        raise ValueError(f"無法解析金額：{text}")
    return int(digits)


def parse_first_balance(rows: list[list[str]]) -> Optional[tuple[str, int]]:
    """rows＝表格每一列的格子文字（含表頭）。回傳第一筆資料的 (處理日期, 本次剩餘金額)；沒有資料回傳 None。"""
    header: list[str] = []
    for raw in rows:
        cells = [re.sub(r"\s+", "", c) for c in raw]
        if cells and _DATE_RE.match(cells[0]):
            money_headers = [h for h in header if h in MONEY_HEADERS]
            if "本次剩餘金額" not in money_headers:
                raise RuntimeError("帳戶明細查詢結果找不到「本次剩餘金額」欄；藍新頁面可能已改版")
            money_cells = [c for c in cells if "NT$" in c]
            index = money_headers.index("本次剩餘金額")
            if index >= len(money_cells):
                raise RuntimeError(f"第一筆資料的金額欄位數不對：{cells}")
            return re.sub(r"\s+", " ", raw[0]).strip(), parse_amount(money_cells[index])
        header += cells
    return None


def query_range(period: str, today: Optional[date] = None) -> tuple[str, str]:
    """自＝期別那個月 1 日，至＝下個月 1 日（不超過今天）。"""
    year, month = int(period[:4]), int(period[4:])
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    end = min(end, today or date.today())
    return start.isoformat(), max(end, start).isoformat()


def query_balance(page, period: str) -> Optional[tuple[str, int]]:
    """在已登入的藍新頁面查帳戶明細，回傳第一筆 (處理日期, 本次剩餘金額)。"""
    from tools.newebpay.invoice_amounts import visible_date_inputs, write_date

    page.goto(BALANCE_URL, wait_until="domcontentloaded")
    dates = visible_date_inputs(page)
    if len(dates) < 2:
        raise RuntimeError(f"找不到帳戶明細查詢的日期起訖欄位（頁面：{page.url}）")
    start, end = query_range(period)
    write_date(dates[0], start)
    write_date(dates[1], end)
    search = page.locator('input[value="開始查詢"], button:has-text("開始查詢")')
    visible = [search.nth(i) for i in range(search.count()) if search.nth(i).is_visible()]
    if not visible:
        raise RuntimeError("找不到「開始查詢」按鈕")
    visible[0].click()
    page.wait_for_timeout(1500)
    try:
        page.get_by_text(re.compile("總查詢筆數")).first.wait_for(timeout=30_000)
    except Exception:
        pass
    tables = page.locator("table")
    for index in range(tables.count()):
        table = tables.nth(index)
        if "本次剩餘金額" not in (table.inner_text() or ""):
            continue
        trs = table.locator("tr")
        rows = [trs.nth(i).locator("th,td").all_inner_texts() for i in range(min(trs.count(), 12))]
        return parse_first_balance(rows)
    if "查無資料" in (page.content() or ""):
        return None
    raise RuntimeError("查詢後找不到帳戶明細表格；藍新頁面可能已改版")


def write_balances(balances: dict[str, tuple[str, int]], service=None, spreadsheet_id: str = "") -> int:
    """寫進「現金缺口試算」的藍新帳務日期／金額兩列（依第 1 列的地區找欄；找不到這兩列就加在最後）。"""
    from tools.finance_management.cash_gap import CASH_GAP_SHEET_NAME, _column_letter

    if service is None:
        from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

        service, spreadsheet_id = get_sheets_service(), get_master_spreadsheet_id()
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{CASH_GAP_SHEET_NAME}'!A1:G40").execute().get("values", [])
    header = [str(v).strip() for v in (values[0] if values else [])]
    labels = [str(r[0]).strip() if r else "" for r in values]
    data = []
    rows = {}
    for label in (DATE_LABEL, AMOUNT_LABEL):
        if label in labels:
            rows[label] = labels.index(label) + 1
        else:
            rows[label] = max(len(labels), 14) + 1 + len([1 for k in rows if k not in labels])
            data.append({"range": f"'{CASH_GAP_SHEET_NAME}'!A{rows[label]}", "values": [[label]]})
            labels += [""] * (rows[label] - len(labels))
            labels[rows[label] - 1] = label
    for area, (when, amount) in balances.items():
        if area not in header:
            continue
        col = _column_letter(header.index(area) + 1)
        data.append({"range": f"'{CASH_GAP_SHEET_NAME}'!{col}{rows[DATE_LABEL]}", "values": [[when]]})
        data.append({"range": f"'{CASH_GAP_SHEET_NAME}'!{col}{rows[AMOUNT_LABEL]}", "values": [[amount]]})
    if data:
        service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body={
            "valueInputOption": "RAW", "data": data}).execute()
    return len(balances)
