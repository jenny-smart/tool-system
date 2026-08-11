from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)
from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service


BASE_URL = "https://www.newebpay.com"
LOGIN_URL = f"{BASE_URL}/main/login_center/single_login"
INVOICE_URL = f"{BASE_URL}/invoice/search_invoice/search_list"
LOGOUT_URL = f"{BASE_URL}/company/company_procedures/logout"
DEFAULT_ACCOUNTS_FILE = Path.home() / "NewebPay account" / "newebpay_accounts.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs"


@dataclass(frozen=True)
class AreaAccount:
    area: str
    company_id: str
    account: str
    password: str


@dataclass(frozen=True)
class InvoiceAmount:
    area: str
    source_month: str
    invoice_date: str
    amount: int | None
    invoice_number: str = ""
    status: str = ""


def normalize_month(value: str) -> str:
    value = re.sub(r"[/]", "", value.strip())
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("月份請輸入 YYYYMM，例如 202606")
    if not 1 <= int(value[4:]) <= 12:
        raise ValueError("月份必須介於 01～12")
    return value


def month_number(yyyymm: str) -> int:
    yyyymm = normalize_month(yyyymm)
    return int(yyyymm[:4]) * 12 + int(yyyymm[4:]) - 1


def month_from_number(value: int) -> str:
    year, month0 = divmod(value, 12)
    return f"{year:04d}{month0 + 1:02d}"


def parse_month_spec(value: str) -> list[str]:
    parts = [part.strip() for part in value.split("-")]
    if len(parts) == 1:
        return [normalize_month(parts[0])]
    if len(parts) != 2:
        raise ValueError("期間格式請用 YYYYMM 或 YYYYMM-YYYYMM")
    start, end = (month_number(part) for part in parts)
    if start > end:
        raise ValueError("起始月份不可晚於結束月份")
    if end - start > 23:
        raise ValueError("一次最多查詢 24 個月份")
    return [month_from_number(value) for value in range(start, end + 1)]


def invoice_date_for(source_month: str) -> str:
    """The invoice shown by NewebPay is dated on the first of the next month."""
    return f"{month_from_number(month_number(source_month) + 1)[:4]}-{month_from_number(month_number(source_month) + 1)[4:]}-01"


def load_accounts(accounts_file: Path, selected_areas: list[str] | None) -> list[AreaAccount]:
    if not accounts_file.exists():
        raise FileNotFoundError(
            f"找不到帳密檔：{accounts_file}\n"
            "請先執行 python3 tools/newebpay/setup_accounts.py"
        )
    raw = json.loads(accounts_file.read_text(encoding="utf-8"))
    rows = raw.get("areas", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("帳密檔格式錯誤：areas 必須是陣列")
    accounts: list[AreaAccount] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        missing = [key for key in ("area", "company_id", "account", "password") if not str(row.get(key, "")).strip()]
        if missing:
            raise ValueError(f"帳密檔 {row.get('area', '未知地區')} 缺少：{', '.join(missing)}")
        accounts.append(
            AreaAccount(
                area=str(row["area"]).strip(),
                company_id=str(row["company_id"]).strip(),
                account=str(row["account"]).strip(),
                password=str(row["password"]),
            )
        )
    if selected_areas:
        wanted = set(selected_areas)
        accounts = [account for account in accounts if account.area in wanted]
        missing_areas = [area for area in selected_areas if area not in {account.area for account in accounts}]
        if missing_areas:
            raise ValueError(f"帳密檔尚未設定地區：{', '.join(missing_areas)}")
    if not accounts:
        raise ValueError("帳密檔沒有可用的地區")
    return accounts


def first_visible(page: Page, selectors: tuple[str, ...], label: str, timeout_ms: int = 10_000):
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        for selector in selectors:
            candidates = page.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        page.wait_for_timeout(100)
    raise RuntimeError(f"找不到可見的{label}")


def fill_enterprise_login(page: Page, account: AreaAccount) -> None:
    # 藍新同時保留桌機／行動版登入分頁，第一個相符元素可能是隱藏的。
    # 必須點實際可見的「企業會員登入」，不可直接使用 locator.first。
    enterprise_tab = first_visible(
        page,
        (
            'a[href="#com_login"]',
            '[data-target="#com_login"]',
            'a:has-text("企業會員登入")',
        ),
        "企業會員登入分頁",
    )
    enterprise_tab.click()

    company_id = first_visible(page, ("#com_login .MoComUbn", ".MoComUbn"), "公司統編欄位")
    login_id = first_visible(page, ("#com_login .MoComId", ".MoComId"), "企業帳號欄位")
    password = first_visible(page, ("#com_login .MoComPwd", ".MoComPwd"), "企業密碼欄位")
    company_id.fill(account.company_id)
    login_id.fill(account.account)
    password.fill(account.password)


def login(page: Page, account: AreaAccount) -> None:
    messages: list[str] = []
    needs_relogin = False

    def handle_dialog(dialog: object) -> None:
        nonlocal needs_relogin
        message = str(getattr(dialog, "message", "")).strip()
        if message:
            messages.append(message)
            print(f"[{account.area}] 藍新訊息：{message}", file=sys.stderr)
            if "MEM50012" in message or "登入已失效" in message:
                needs_relogin = True
        getattr(dialog, "accept")()

    page.on("dialog", handle_dialog)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    fill_enterprise_login(page, account)
    print(f"\n[{account.area}] 帳密已填入，請在 Chrome 輸入驗證碼並按企業會員登入。")

    deadline = time.monotonic() + 300
    unexpected_page_since: float | None = None
    while time.monotonic() < deadline:
        # 失效訊息的優先度高於網址；藍新可能先短暫進後台，再彈出 MEM50012。
        if needs_relogin:
            if "/main/login_center/single_login" not in page.url:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
            fill_enterprise_login(page, account)
            needs_relogin = False
            print(f"[{account.area}] 舊登入已失效，帳密已重新填入；請輸入新的驗證碼並再次登入。")
            page.wait_for_timeout(500)
            continue

        # 只有真正進入會員後台才算成功；首頁或錯誤頁不能視為已登入。
        if any(path in page.url for path in ("/sale/", "/invoice/", "/payment/")):
            page.wait_for_timeout(500)
            if needs_relogin:
                continue
            return

        if "/main/login_center/single_login" not in page.url:
            if unexpected_page_since is None:
                unexpected_page_since = time.monotonic()
            elif time.monotonic() - unexpected_page_since >= 2:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
                fill_enterprise_login(page, account)
                unexpected_page_since = None
                print(f"[{account.area}] 尚未成功進入後台，已回登入頁；請輸入新的驗證碼。")
                continue
        else:
            unexpected_page_since = None

        try:
            company_id = first_visible(
                page,
                ("#com_login .MoComUbn", ".MoComUbn"),
                "公司統編欄位",
                timeout_ms=500,
            )
        except RuntimeError:
            company_id = None
        if company_id is not None and not company_id.input_value().strip():
            fill_enterprise_login(page, account)
            print(f"[{account.area}] 已重新填入帳密，請輸入新的驗證碼。")
        page.wait_for_timeout(500)
    detail = f"；最後訊息：{messages[-1]}" if messages else ""
    raise TimeoutError(f"{account.area} 等待登入逾時{detail}")


def write_date(locator: object, value: str) -> None:
    locator.evaluate(
        "(el, value) => { el.removeAttribute('readonly'); el.value = value; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
        value,
    )


def visible_date_inputs(page: Page) -> list[object]:
    result: list[object] = []
    inputs = page.locator("input")
    for index in range(inputs.count()):
        item = inputs.nth(index)
        if not item.is_visible():
            continue
        current = item.input_value().strip()
        hints = " ".join(
            filter(
                None,
                (
                    item.get_attribute("name"),
                    item.get_attribute("id"),
                    item.get_attribute("class"),
                    item.get_attribute("placeholder"),
                ),
            )
        ).lower()
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", current) or re.search(r"date|start|end|日期|起日|迄日", hints):
            result.append(item)
    return result


def query_invoice_rows(page: Page, wanted_dates: list[str]) -> list[dict[str, str]]:
    page.goto(INVOICE_URL, wait_until="domcontentloaded")
    dates = visible_date_inputs(page)
    if len(dates) < 2:
        raise RuntimeError(f"找不到發票日期起訖欄位（頁面：{page.url}）")
    write_date(dates[0], min(wanted_dates))
    write_date(dates[1], max(wanted_dates))

    search = page.locator('input[value="開始查詢"], button:has-text("開始查詢")')
    visible_search = [search.nth(i) for i in range(search.count()) if search.nth(i).is_visible()]
    if len(visible_search) != 1:
        raise RuntimeError(f"找不到唯一的開始查詢按鈕（找到 {len(visible_search)} 個）")
    visible_search[0].click()

    # 藍新以 AJAX 填入結果後，還會用動畫展開表格。資料已存在時，
    # table 可能暫時被壓成數像素高，不能用 is_visible() 判斷是否有結果。
    result_table = page.locator("#table_area_trans table")
    try:
        # 等表格展開完成，避免 AJAX 已回傳但動畫仍收合時誤判失敗。
        result_table.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        count = page.locator("#data_count")
        if count.count() and count.inner_text().strip() == "0":
            return []
        raise RuntimeError("等待藍新發票查詢結果逾時") from exc

    tables = page.locator("table")
    for index in range(tables.count()):
        table = tables.nth(index)
        headers = [re.sub(r"\s+", "", text) for text in table.locator("tr").first.locator("th,td").all_inner_texts()]
        if "發票日期" not in headers or "發票金額" not in headers:
            continue
        rows: list[dict[str, str]] = []
        date_index, amount_index = headers.index("發票日期"), headers.index("發票金額")
        number_index = headers.index("發票號碼") if "發票號碼" in headers else -1
        status_index = headers.index("發票狀態") if "發票狀態" in headers else -1
        body_rows = table.locator("tr")
        for row_index in range(1, body_rows.count()):
            cells = body_rows.nth(row_index).locator("td").all_inner_texts()
            if len(cells) <= max(date_index, amount_index):
                continue
            row_date = cells[date_index].strip().replace("/", "-")
            if row_date not in wanted_dates:
                continue
            rows.append(
                {
                    "invoice_date": row_date,
                    "amount": cells[amount_index].strip(),
                    "invoice_number": cells[number_index].strip() if number_index >= 0 and number_index < len(cells) else "",
                    "status": cells[status_index].strip() if status_index >= 0 and status_index < len(cells) else "",
                }
            )
        return rows
    raise RuntimeError("查詢後找不到發票結果表格；藍新頁面可能已改版")


def parse_amount(value: str) -> int:
    digits = re.sub(r"[^0-9-]", "", value)
    if not digits or digits == "-":
        raise ValueError(f"無法解析發票金額：{value}")
    return int(digits)


def logout(page: Page, area: str) -> None:
    page.goto(LOGOUT_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_url("**/main/login_center/single_login**", timeout=30_000)
    except Exception:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
    print(f"[{area}] 藍新已登出。")


def query_account(page: Page, account: AreaAccount, months: list[str], slow_mo: int) -> list[InvoiceAmount]:
    wanted = {invoice_date_for(month): month for month in months}
    completed = False
    try:
        login(page, account)
        rows = query_invoice_rows(page, list(wanted))
        by_date = {row["invoice_date"]: row for row in rows}
        result = [
            InvoiceAmount(
                area=account.area,
                source_month=month,
                invoice_date=invoice_date,
                amount=parse_amount(by_date[invoice_date]["amount"]) if invoice_date in by_date else None,
                invoice_number=by_date.get(invoice_date, {}).get("invoice_number", ""),
                status=by_date.get(invoice_date, {}).get("status", "查無發票"),
            )
            for invoice_date, month in wanted.items()
        ]
        completed = True
        return result
    except Exception:
        debug_dir = DEFAULT_OUTPUT_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=debug_dir / f"invoice-{account.area}.png", full_page=True)
        (debug_dir / f"invoice-{account.area}.html").write_text(page.content(), encoding="utf-8")
        raise
    finally:
        if slow_mo:
            page.wait_for_timeout(slow_mo)
        try:
            logout(page, account.area)
        except Exception as exc:
            if completed:
                raise
            print(f"[{account.area}] 登出失敗：{exc}", file=sys.stderr)


def save_csv(rows: list[InvoiceAmount], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["地區", "歸屬月份", "發票日期", "發票金額", "發票號碼", "發票狀態"])
        for row in rows:
            writer.writerow([row.area, row.source_month, row.invoice_date, row.amount if row.amount is not None else "", row.invoice_number, row.status])


def sync_fee_sheets(rows: list[InvoiceAmount]) -> tuple[int, list[str]]:
    """單一總表保存結果，每次查詢往下追加。"""
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    title = "藍新發票總表"
    headers = ["區域", "月份", "發票日期", "發票金額", "發票號碼", "發票狀態", "更新時間"]
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    sheet_ids = {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in meta.get("sheets", [])
    }
    if title not in sheet_ids:
        response = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title, "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
        sheet_ids[title] = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A1:G1",
        valueInputOption="RAW", body={"values": [headers]},
    ).execute()
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = [[row.area, row.source_month, row.invoice_date,
        row.amount if row.amount is not None else "", row.invoice_number, row.status, updated_at]
        for row in rows]
    if values:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"'{title}'!A:G",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
        "repeatCell": {"range": {"sheetId": sheet_ids[title], "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}
    }]}).execute()
    return len(rows), [title]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查詢藍新各區指定月份的發票金額")
    parser.add_argument("months", help="YYYYMM 或 YYYYMM-YYYYMM，例如 202605-202606")
    parser.add_argument("--area", nargs="+", help="地區，可輸入多個，例如 --area 台北 台中；省略表示全部")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--output", type=Path, help="輸出 CSV 路徑")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--slow-mo", type=int, default=0, help="每區完成後額外停留毫秒數")
    parser.add_argument("--dry-run", action="store_true", help="只顯示查詢計畫，不開啟瀏覽器")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        months = parse_month_spec(args.months)
        accounts = load_accounts(args.accounts_file.expanduser(), args.area)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2

    dates = [invoice_date_for(month) for month in months]
    print(f"地區：{', '.join(account.area for account in accounts)}")
    print(f"歸屬月份：{', '.join(months)}")
    print(f"藍新發票日：{', '.join(dates)}")
    print("查詢方式：每區只登入一次、每區只送出一次日期範圍查詢")
    if args.dry_run:
        return 0

    results: list[InvoiceAmount] = []
    failures: list[str] = []
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        shared_page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        try:
            for account in accounts:
                try:
                    results.extend(query_account(shared_page, account, months, args.slow_mo))
                except Exception as exc:
                    failures.append(f"{account.area}: {exc}")
                    print(f"[{account.area}] 查詢失敗：{exc}", file=sys.stderr)
        finally:
            if not shared_page.is_closed():
                shared_page.close()
                print("藍新視窗已關閉。")
        if results:
            print("\n查詢結果：")
            for row in results:
                amount = f"NT$ {row.amount:,}" if row.amount is not None else "查無發票"
                print(f"{row.area}  {row.source_month}  {row.invoice_date}  {amount}")

    written, sheet_titles = sync_fee_sheets(results)
    print(f"藍新發票獨立工作表寫入 {written} 筆：{', '.join(sheet_titles)}")
    if results:
        if args.output:
            save_csv(results, args.output)
            print(f"\nCSV：{args.output}")
            print(f"RESULT_FILE:{args.output.resolve()}")
    if failures:
        print("\n未完成：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
