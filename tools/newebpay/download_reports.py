from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)


BASE_URL = "https://www.newebpay.com"
LOGIN_URL = f"{BASE_URL}/main/login_center/single_login"
PAYMENT_URL = f"{BASE_URL}/sale/Sell_center/search_transaction?count=1"
REFUND_URL = f"{BASE_URL}/payment/Credit_finance/search_credit_close"
DEFAULT_ACCOUNTS_FILE = Path.home() / "NewebPay account" / "newebpay_accounts.json"
BASE_GOOGLE_DRIVE = (
    Path.home()
    / "Library"
    / "CloudStorage"
    / "GoogleDrive-jenny@lemonclean.com.tw"
    / "我的雲端硬碟"
)
PATH_HR = BASE_GOOGLE_DRIVE / "lemon_人事" / "03 服務分潤表"
AREA_OUTPUT_DIRS = {
    "台北": "01.台北專員",
    "台中": "02.台中專員",
    "新北": "03.新北專員",
    "桃園": "04.桃園專員",
    "新竹": "05.新竹專員",
    "高雄": "06.高雄專員",
    "家電": "07.家電專員",
}


@dataclass(frozen=True)
class AreaAccount:
    area: str
    company_id: str
    account: str
    password: str
    merchant_label: str = ""


def normalize_month(value: str) -> str:
    value = re.sub(r"[-/]", "", str(value).strip())
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("月份請輸入 YYYYMM，例如 202606")
    month = int(value[4:])
    if month < 1 or month > 12:
        raise ValueError("月份必須介於 01～12")
    return value


def date_range(yyyymm: str, half: str) -> tuple[str, str, str]:
    yyyymm = normalize_month(yyyymm)
    year, month = int(yyyymm[:4]), int(yyyymm[4:])
    last_day = calendar.monthrange(year, month)[1]
    if half == "1":
        start_day, end_day, period = 1, 15, f"{yyyymm}-1"
    elif half == "2":
        start_day, end_day, period = 16, last_day, f"{yyyymm}-2"
    else:
        start_day, end_day, period = 1, last_day, yyyymm
    return (
        f"{year:04d}-{month:02d}-{start_day:02d}",
        f"{year:04d}-{month:02d}-{end_day:02d}",
        period,
    )


def explicit_date_range(start: str, end: str) -> tuple[str, str, str]:
    try:
        start_date = datetime.strptime(start.replace("/", "-"), "%Y-%m-%d").date()
        end_date = datetime.strptime(end.replace("/", "-"), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期格式請使用 YYYY-MM-DD") from exc
    if start_date > end_date:
        raise ValueError("開始日期不可晚於結束日期")
    if (start_date.year, start_date.month) != (end_date.year, end_date.month):
        raise ValueError("藍新下載的開始與結束日期必須在同一月份")
    period = f"{start_date:%Y%m}-2"
    return start_date.isoformat(), end_date.isoformat(), period


def load_account(raw: dict[str, object], source: Path) -> AreaAccount:
    missing = [key for key in ("area", "company_id", "account", "password") if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValueError(f"{source} 缺少欄位：{', '.join(missing)}")
    return AreaAccount(
        area=str(raw["area"]).strip(),
        company_id=str(raw["company_id"]).strip(),
        account=str(raw["account"]).strip(),
        password=str(raw["password"]),
        merchant_label=str(raw.get("merchant_label", "")).strip(),
    )


def load_accounts(accounts_file: Path, selected_area: str | None) -> list[AreaAccount]:
    if not accounts_file.exists():
        raise FileNotFoundError(f"找不到帳密檔：{accounts_file}\n請先執行 python3 tools/newebpay/setup_accounts.py")
    raw = json.loads(accounts_file.read_text(encoding="utf-8"))
    rows = raw.get("areas", raw) if isinstance(raw, dict) else raw
    if isinstance(rows, dict):
        rows = [dict(value, area=value.get("area") or area) for area, value in rows.items() if isinstance(value, dict)]
    if not isinstance(rows, list):
        raise ValueError(f"{accounts_file} 格式錯誤，areas 必須是陣列或物件")
    accounts = [load_account(row, accounts_file) for row in rows if isinstance(row, dict)]
    if selected_area:
        accounts = [item for item in accounts if item.area == selected_area]
    if not accounts:
        suffix = f"（區域：{selected_area}）" if selected_area else ""
        raise RuntimeError(f"帳密檔內沒有可用區域{suffix}")
    return accounts


def area_output_dir(area: str, period: str, override: Path | None = None) -> Path:
    if override:
        return override.expanduser() / AREA_OUTPUT_DIRS.get(area, area) / period
    area_dir = AREA_OUTPUT_DIRS.get(area)
    if not area_dir:
        raise ValueError(f"尚未設定 {area} 的人事輸出資料夾")
    year = period[:4]
    return PATH_HR / f"{year}專員承攬服務費" / area_dir / period


def first_visible(page: Page, selectors: Iterable[str]) -> Locator:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            item = locator.nth(index)
            if item.is_visible():
                return item
    raise RuntimeError("找不到下載按鈕；藍新頁面可能已改版")


def set_date(page: Page, names: Iterable[str], value: str, position: int) -> None:
    for name in names:
        locator = page.locator(f'input[name="{name}"], input#{name}')
        if locator.count() and locator.first.is_visible():
            write_date(locator.first, value)
            return

    # 藍新不同頁面使用過不同的 id/name，但日期欄位都有 datepicker
    # class 或 YYYY-MM-DD 值；以畫面上第 1、2 個日期欄位作為起訖日。
    candidates: list[Locator] = []
    date_inputs = page.locator("input")
    for index in range(date_inputs.count()):
        item = date_inputs.nth(index)
        if not item.is_visible():
            continue
        input_type = (item.get_attribute("type") or "text").lower()
        if input_type not in {"text", "date", "search"}:
            continue
        current = item.input_value().strip()
        placeholder = (item.get_attribute("placeholder") or "").strip()
        hints = " ".join(
            filter(
                None,
                (
                    item.get_attribute("name"),
                    item.get_attribute("id"),
                    item.get_attribute("class"),
                    placeholder,
                    item.get_attribute("aria-label"),
                ),
            )
        ).lower()
        looks_like_date = bool(
            re.fullmatch(r"\d{4}\D?\d{1,2}\D?\d{1,2}", current)
            or re.search(r"date|period|start|end|time|日期|期間|起日|迄日", hints)
        )
        if looks_like_date:
            candidates.append(item)
    if len(candidates) >= 2:
        write_date(candidates[position], value)
        return
    visible_inputs: list[str] = []
    for index in range(min(date_inputs.count(), 30)):
        item = date_inputs.nth(index)
        if not item.is_visible():
            continue
        visible_inputs.append(
            "/".join(
                filter(
                    None,
                    (
                        item.get_attribute("type") or "text",
                        item.get_attribute("name"),
                        item.get_attribute("id"),
                        item.get_attribute("class"),
                        item.get_attribute("placeholder"),
                    ),
                )
            )
        )
    raise RuntimeError(
        f"找不到起訖日期欄位（找到 {len(candidates)} 個日期輸入框）"
        f"\n頁面：{page.url}"
        f"\n可見輸入框：{visible_inputs}"
    )


def write_date(locator: Locator, value: str) -> None:
    locator.evaluate(
        "(el, value) => { el.removeAttribute('readonly'); el.value = value; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
        value,
    )


def check_label(page: Page, label_text: str) -> None:
    labels = page.locator("label").filter(has_text=label_text)
    for index in range(labels.count()):
        label = labels.nth(index)
        if not label.is_visible():
            continue
        control = label.locator('input[type="radio"], input[type="checkbox"]')
        if control.count():
            control.first.check(force=True)
            return
    raise RuntimeError(f"找不到選項：{label_text}")


def select_merchant(page: Page, account: AreaAccount) -> None:
    if not account.merchant_label or account.merchant_label == account.area:
        return
    selects = page.locator('select[name="MerID"], select#selectInfo')
    visible_selects = [selects.nth(i) for i in range(selects.count()) if selects.nth(i).is_visible()]
    if not visible_selects:
        raise RuntimeError("設定了 merchant_label，但頁面找不到商店代號欄位")
    select = visible_selects[0]
    options = select.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        if account.merchant_label in option.inner_text():
            select.select_option(option.get_attribute("value") or "")
            return
    raise RuntimeError(f"商店選單找不到：{account.merchant_label}")


def fill_enterprise_login(page: Page, account: AreaAccount) -> None:
    enterprise_tab = page.locator('a[href="#com_login"]')
    if enterprise_tab.count() and enterprise_tab.first.is_visible():
        enterprise_tab.first.click()
    page.locator(".MoComUbn").fill(account.company_id)
    page.locator(".MoComId").fill(account.account)
    page.locator(".MoComPwd").fill(account.password)


def login(page: Page, account: AreaAccount) -> list[str]:
    login_messages: list[str] = []

    def show_dialog(dialog: object) -> None:
        message = str(getattr(dialog, "message", "")).strip()
        if message:
            login_messages.append(message)
            print(f"\n[{account.area}] 藍新訊息：{message}", file=sys.stderr)
        getattr(dialog, "accept")()

    def show_login_response(response: object) -> None:
        url = str(getattr(response, "url", ""))
        if "company_login_check" in url:
            print(f"[{account.area}] 藍新已接收登入資料（HTTP {getattr(response, 'status', '?')}）")

    page.on("dialog", show_dialog)
    page.on("response", show_login_response)
    if "newebpay.com" in page.url and "/main/login_center/single_login" not in page.url:
        print(f"[{account.area}] 沿用目前藍新登入狀態。")
        return login_messages
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    fill_enterprise_login(page, account)

    print(f"\n[{account.area}] 企業帳密已預填。")
    print("請直接在網頁輸入驗證碼，並點擊「企業會員登入」。")
    print("若被退回登入頁，終端機會顯示藍新的錯誤訊息，並可重新輸入新驗證碼。")

    deadline = time.monotonic() + 300
    refilled_after_failure = False
    while time.monotonic() < deadline:
        if "/sale/Sell_center/search_transaction" in page.url:
            return login_messages
        if "/main/login_center/single_login" in page.url:
            company_id = page.locator(".MoComUbn")
            if company_id.count() and company_id.first.is_visible():
                if not company_id.first.input_value().strip():
                    fill_enterprise_login(page, account)
                    if login_messages or refilled_after_failure:
                        print(f"[{account.area}] 已重新填入企業帳密，請輸入網頁上的新驗證碼。")
                    refilled_after_failure = True
        page.wait_for_timeout(500)

    detail = f"；最後訊息：{login_messages[-1]}" if login_messages else ""
    raise RuntimeError(f"{account.area} 等待登入逾時，目前頁面：{page.url}{detail}")


def no_transaction_data(messages: list[str], start_index: int) -> bool:
    return any("查無交易資料" in message or "查無資料" in message for message in messages[start_index:])


def save_empty_csv(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8-sig")
    print(f"  查無資料，已建立空白檔：{target}")


def click_search(page: Page) -> None:
    buttons = page.locator('input[value="開始查詢"], button:has-text("開始查詢")')
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible():
            button.click()
            page.wait_for_timeout(1_000)
            return
    raise RuntimeError("找不到「開始查詢」按鈕")


def save_download(page: Page, selectors: Iterable[str], target: Path) -> None:
    button = first_visible(page, selectors)
    target.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=30_000) as info:
        button.click()
    info.value.save_as(target)
    print(f"  已下載：{target}")


def download_payments(
    page: Page,
    account: AreaAccount,
    start: str,
    end: str,
    target: Path,
    messages: list[str],
) -> None:
    if "/sale/Sell_center/search_transaction" not in page.url:
        page.goto(PAYMENT_URL, wait_until="domcontentloaded")
    form = page.locator("#trans_form")
    if not form.count():
        raise RuntimeError(f"找不到收款查詢表單 #trans_form，頁面：{page.url}")
    write_date(form.locator('input[name="PeriodStartDate"]'), start)
    write_date(form.locator('input[name="PeriodEndDate"]'), end)

    advanced = page.locator("#trans_open")
    if advanced.count() and advanced.is_visible():
        advanced.click()
    select_merchant(page, account)
    form.locator('input[name="PayStatus"][value="1"]').check(force=True)
    search_button = page.locator('#trans_search input[value="開始查詢"]')
    if not search_button.count():
        raise RuntimeError("找不到收款「開始查詢」按鈕")
    message_index = len(messages)
    search_button.click()
    page.wait_for_timeout(1_000)
    if no_transaction_data(messages, message_index):
        save_empty_csv(target)
        return
    page.locator("#download_trans").wait_for(state="visible", timeout=30_000)
    save_download(
        page,
        (
            "#download_trans",
        ),
        target,
    )


def download_refunds(
    page: Page,
    account: AreaAccount,
    start: str,
    end: str,
    target: Path,
    messages: list[str],
) -> None:
    page.goto(REFUND_URL, wait_until="domcontentloaded")
    page.locator('select[name="SearchMainType"]').select_option("2")
    set_date(page, ("PeriodStartDate", "per_start_date", "StartDate", "start_date"), start, 0)
    set_date(page, ("PeriodEndDate", "per_end_date", "EndDate", "end_date"), end, 1)
    select_merchant(page, account)
    page.locator('input[name="ProcessType"][value=""]').check(force=True)
    message_index = len(messages)
    click_search(page)
    if no_transaction_data(messages, message_index):
        save_empty_csv(target)
        return
    page.locator("#download_close_trans").wait_for(state="visible", timeout=30_000)
    save_download(
        page,
        (
            "#download_close_trans",
            "#download_div input",
            "#download_div button",
            "#download_div a",
            'input[value*="下載"]',
            'button:has-text("下載")',
            'a:has-text("下載")',
        ),
        target,
    )


def run_account(
    context: BrowserContext,
    account: AreaAccount,
    start: str,
    end: str,
    period: str,
    output: Path,
    page: Page | None = None,
) -> list[Path]:
    owns_page = page is None
    page = page or context.new_page()
    payment_path = output / f"{period}藍新收款-{account.area}.csv"
    refund_path = output / f"{period}藍新退款-{account.area}.csv"
    try:
        messages = login(page, account)
        download_payments(
            page,
            account,
            start,
            end,
            payment_path,
            messages,
        )
        download_refunds(
            page,
            account,
            start,
            end,
            refund_path,
            messages,
        )
    except Exception:
        debug = output / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=debug / f"{period}-{account.area}.png", full_page=True)
        (debug / f"{period}-{account.area}.html").write_text(page.content(), encoding="utf-8")
        raise
    finally:
        if owns_page:
            page.close()
    return [payment_path, refund_path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下載藍新各區收款與退款 CSV")
    parser.add_argument("month", nargs="?", help="年月 YYYYMM，例如 202606")
    parser.add_argument("--start-date", help="開始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", help="結束日期 YYYY-MM-DD")
    parser.add_argument("--half", choices=("1", "2", "full"), default="full", help="full=整月（預設）；1=1～15日；2=16～月底")
    parser.add_argument("--area", help="只下載指定區域，例如 台北")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE, help=f"各區帳密 JSON（預設：{DEFAULT_ACCOUNTS_FILE}）")
    parser.add_argument("--output", type=Path, help="自訂輸出根目錄；未指定時存到 Google Drive 人事目錄")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start_date or args.end_date:
        if not args.start_date or not args.end_date:
            raise ValueError("--start-date 與 --end-date 必須同時提供")
        start, end, period = explicit_date_range(args.start_date, args.end_date)
    else:
        if not args.month:
            raise ValueError("請提供月份或日期區間")
        start, end, period = date_range(args.month, args.half)
    accounts = load_accounts(args.accounts_file.expanduser(), args.area)
    print(f"日期：{start} ～ {end}")
    print(f"區域：{', '.join(account.area for account in accounts)}")

    failures: list[str] = []
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        existing_page = find_existing_page(context, ("newebpay.com",))
        try:
            for index, account in enumerate(accounts):
                print(f"\n開始處理：{account.area}")
                try:
                    output = area_output_dir(account.area, period, args.output)
                    print(f"儲存位置：{output}")
                    page = existing_page if index == 0 and existing_page is not None else None
                    files = run_account(context, account, start, end, period, output, page=page)
                    for file_path in files:
                        print(f"RESULT_FILE: {file_path}")
                except Exception as exc:
                    failures.append(f"{account.area}: {exc}")
                    print(f"  失敗：{exc}", file=sys.stderr)
        finally:
            pass

    if failures:
        print("\n未完成：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\n全部區域下載完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
