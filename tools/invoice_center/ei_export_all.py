from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center.config import AREA_ENV, EICredentials, normalize_area
    from invoice_center.query import to_roc_date
else:
    from .config import AREA_ENV, EICredentials, normalize_area
    from .query import to_roc_date


PORTAL_LOGIN_URL = "https://www.cetustek.com.tw/memberlogin.php"
PORTAL_MEMBER_URL = "https://www.cetustek.com.tw/member.php"
EI_LOGIN_URL = "https://www.ei.com.tw/InvoiceRent/index.jsp"
EI_EXPORT_URL = "https://www.ei.com.tw/InvoiceRent/invoiceexport.jsp"
DEFAULT_CHROME_PROFILE = Path.home() / "EI account" / "chrome_profile"

DEFAULT_ACCOUNT_PATHS = [
    Path.home() / "EI account" / "ei_accounts.json",
    Path.home() / "EI_account" / "ei_accounts.json",
    Path.home() / ".lemon_ei_accounts.json",
    Path(__file__).with_name("ei_accounts.json"),
]

BASE_GOOGLE_DRIVE = (
    Path.home()
    / "Library"
    / "CloudStorage"
    / "GoogleDrive-jenny@lemonclean.com.tw"
    / "我的雲端硬碟"
)
PATH_HR = BASE_GOOGLE_DRIVE / "lemon_人事" / "03 服務分潤表"

AREA_OUTPUT_DIRS = {
    "taipei": "02.台北專員",
    "taichung": "02.台中專員",
    "new_taipei": "03.新北專員",
    "taoyuan": "04.桃園專員",
    "hsinchu": "05.新竹專員",
    "kaohsiung": "06.高雄專員",
}


def normalize_month(value: str) -> str:
    text = re.sub(r"[-/]", "", str(value or "").strip())
    if not re.fullmatch(r"\d{6}", text):
        raise ValueError("年月請輸入 YYYYMM，例如 202606")
    if not 1 <= int(text[4:]) <= 12:
        raise ValueError("月份必須介於 01～12")
    return text


def month_range(yyyymm: str) -> tuple[str, str]:
    text = normalize_month(yyyymm)
    year, month = int(text[:4]), int(text[4:])
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}/{month:02d}/01", f"{year:04d}/{month:02d}/{last_day:02d}"


def bi_month_folder(yyyymm: str) -> str:
    month = int(normalize_month(yyyymm)[4:])
    start = month if month % 2 == 1 else month - 1
    return f"{start:02d}-{start + 1:02d}"


def load_accounts(path: Path | None = None) -> dict[str, Any]:
    candidates = [path.expanduser()] if path else DEFAULT_ACCOUNT_PATHS
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    searched = "\n".join(str(item) for item in DEFAULT_ACCOUNT_PATHS)
    raise FileNotFoundError(f"找不到 EI 帳密檔：\n{searched}")


def credentials_for(area: str, accounts: dict[str, Any]) -> EICredentials:
    area_key = normalize_area(area)
    meta = AREA_ENV[area_key]
    row = accounts.get(area_key) or accounts.get(meta["label"]) or {}
    userid = str(row.get("userid") or row.get("user") or "").strip()
    password = str(row.get("password") or "")
    if not userid or not password:
        raise RuntimeError(f"{meta['label']} 缺 userid/password")
    return EICredentials(
        area=area_key,
        label=str(row.get("label") or meta["label"]),
        userid=userid,
        password=password,
        entry_url=str(row.get("entry_url") or "").strip(),
    )


def configured_areas(accounts: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for area_key, meta in AREA_ENV.items():
        row = accounts.get(area_key) or accounts.get(meta["label"]) or {}
        if str(row.get("userid") or row.get("user") or "").strip() and str(row.get("password") or ""):
            result.append(area_key)
    if not result:
        raise RuntimeError("EI 帳密檔沒有任何已設定區域")
    return result


def output_dir(area: str, accounts: dict[str, Any], yyyymm: str, override: Path | None) -> Path:
    area_key = normalize_area(area)
    meta = AREA_ENV[area_key]
    root = override.expanduser() if override else PATH_HR / f"{yyyymm[:4]}專員承攬服務費"
    label_suffix = f".{meta['label']}專員"
    try:
        matches = [item for item in root.iterdir() if item.is_dir() and item.name.endswith(label_suffix)]
    except OSError:
        matches = []
    if len(matches) == 1:
        area_root = matches[0]
    elif len(matches) > 1:
        raise RuntimeError(f"{root} 找到多個 {meta['label']} 資料夾：{matches}")
    else:
        area_root = root / AREA_OUTPUT_DIRS.get(area_key, f"{meta['label']}專員")
    return area_root / f"{yyyymm}-2"


def install_dialog_log(page: Page, label: str) -> None:
    def handle_dialog(dialog: object) -> None:
        message = str(getattr(dialog, "message", "")).strip()
        if message:
            print(f"\n[{label}] 網頁訊息：{message}", file=sys.stderr)
        getattr(dialog, "accept")()

    page.on("dialog", handle_dialog)


def portal_values(accounts: dict[str, Any]) -> tuple[str, str, str]:
    common = accounts.get("common") if isinstance(accounts.get("common"), dict) else {}
    taipei = accounts.get("taipei") if isinstance(accounts.get("taipei"), dict) else {}
    company_id = str(
        common.get("portal_company_id")
        or common.get("company_id")
        or common.get("vat")
        or taipei.get("company_id")
        or taipei.get("vat")
        or taipei.get("userid")
        or ""
    ).strip()
    member_id = str(
        common.get("portal_account")
        or common.get("portal_userid")
        or common.get("memberid")
        or taipei.get("portal_account")
        or taipei.get("account")
        or ""
    ).strip()
    password = str(common.get("portal_password") or taipei.get("password") or "")
    return company_id, member_id, password


def fill_if_present(page: Page, selector: str, value: str) -> None:
    locator = page.locator(selector)
    if value and locator.count() and locator.first.is_visible():
        locator.first.fill(value)


def login_portal(page: Page, accounts: dict[str, Any]) -> None:
    install_dialog_log(page, "第一層")
    # 使用會保留 Cookie 的專用 Chrome 設定檔。已登入過第一層時，
    # member.php 會直接開啟會員頁，不必再次輸入帳密與驗證碼。
    page.goto(PORTAL_MEMBER_URL, wait_until="domcontentloaded")
    try:
        find_ei_link(page)
        print("[第一層] 沿用 Chrome 登入狀態")
        return
    except RuntimeError:
        pass
    if "memberlogin.php" not in page.url:
        page.goto(PORTAL_LOGIN_URL, wait_until="domcontentloaded")
    company_id, member_id, password = portal_values(accounts)
    fill_if_present(page, "#txtvat", company_id)
    fill_if_present(page, "#txtmemberid", member_id)
    fill_if_present(page, "#txtpasswd", password)

    if company_id and password:
        print("\n[第一層] 帳密已預填，請在網頁輸入驗證碼並點擊「登入」。")
    else:
        print("\n[第一層] 尚未設定入口帳密，請在網頁輸入統編、帳號、密碼、驗證碼並登入。")
        print("登入後可把 portal_company_id / portal_account / portal_password 加到帳密檔 common。")

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            find_ei_link(page)
            print("[第一層] 登入成功")
            return
        except RuntimeError:
            pass
        error = page.locator(".alertify-log, .ajs-message, .alert-danger")
        if error.count() and error.last.is_visible():
            message = error.last.inner_text().strip()
            if message:
                print(f"[第一層] {message}", file=sys.stderr)
        page.wait_for_timeout(500)
    raise RuntimeError(f"第一層登入等待逾時，目前頁面：{page.url}")


def find_ei_link(page: Page):
    direct = page.locator('a[href*="ei.com.tw/InvoiceRent"], a[href*="InvoiceRent"]')
    for index in range(direct.count()):
        if direct.nth(index).is_visible():
            return direct.nth(index)
    system_text = page.get_by_text(re.compile(r"電子發票.*雲端A"))
    for index in range(system_text.count()):
        row = system_text.nth(index).locator("xpath=ancestor::tr[1]")
        link = row.locator("a")
        if link.count() and link.first.is_visible():
            return link.first
    raise RuntimeError("第一層會員頁找不到「電子發票-雲端A」連結")


def open_second_login(context: BrowserContext, portal_page: Page) -> Page:
    link = find_ei_link(portal_page)
    href = (link.get_attribute("href") or "").strip()
    if href.startswith("http"):
        page = context.new_page()
        page.goto(href, wait_until="domcontentloaded")
        return page

    old_pages = set(context.pages)
    link.click()
    portal_page.wait_for_timeout(1_500)
    new_pages = [page for page in context.pages if page not in old_pages]
    return new_pages[-1] if new_pages else portal_page


def login_second(page: Page, credentials: EICredentials) -> None:
    install_dialog_log(page, credentials.label)
    page.wait_for_load_state("domcontentloaded")
    if page.locator("#userid").count() == 0:
        page.goto(EI_LOGIN_URL, wait_until="domcontentloaded")
    page.locator("#userid").fill(credentials.userid)
    page.locator("#pwd").fill(credentials.password)
    print(f"[{credentials.label}] 第二層帳密已預填，請在網頁輸入驗證碼並點擊「登入」。")

    deadline = time.monotonic() + 300
    last_message = ""
    while time.monotonic() < deadline:
        login_field = page.locator("#userid")
        if not login_field.count() or not login_field.first.is_visible():
            print(f"[{credentials.label}] 第二層登入成功")
            return
        message_field = page.locator("#msg")
        if message_field.count():
            message = message_field.first.inner_text().strip()
            if message and message != last_message:
                last_message = message
                print(f"[{credentials.label}] 登入訊息：{message}", file=sys.stderr)
        if not login_field.first.input_value().strip():
            login_field.first.fill(credentials.userid)
            page.locator("#pwd").fill(credentials.password)
            print(f"[{credentials.label}] 已重新填入帳密，請輸入新的網頁驗證碼。")
        page.wait_for_timeout(500)
    raise RuntimeError(f"{credentials.label} 第二層登入等待逾時，目前頁面：{page.url}；{last_message}")


def logout_second(page: Page, label: str) -> None:
    try:
        page.evaluate("() => { if (typeof DirectLogOut === 'function') DirectLogOut(); }")
        page.wait_for_url("**/InvoiceRent/index.jsp", timeout=30_000)
        print(f"[{label}] 第二層已登出")
    except Exception as exc:
        raise RuntimeError(f"{label} 第二層登出失敗：{exc}") from exc


def set_readonly_value(page: Page, selector: str, value: str) -> None:
    page.locator(selector).evaluate(
        "(el, value) => { el.removeAttribute('readonly'); el.value = value; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
        value,
    )


def detect_download_format(path: Path) -> str:
    """依檔案內容判斷下載結果，不相信副檔名。"""
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("EI 下載結果是空檔案")

    if zipfile.is_zipfile(path):
        return "zip"

    header = path.read_bytes()[:8192]
    lowered = header.lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in lowered:
        preview = header.decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
        preview = re.sub(r"\s+", " ", preview).strip()[:300]
        raise RuntimeError(f"EI 回傳的是網頁而不是匯出檔，可能登入失效或網站報錯：{preview}")

    # 舊式 Excel .xls 使用 OLE Compound File magic bytes。
    if header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return "xls"

    # 新式 Excel .xlsx 本身也是 ZIP；上方已歸類 zip。EI 現行選項通常是 .xls。
    if b"\x00" not in header:
        for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                text = header.decode(encoding)
            except UnicodeDecodeError:
                continue
            if "," in text or "\t" in text or "發票" in text:
                return "csv"

    return "unknown"


def final_download_path(target: Path, detected: str, suggested_filename: str) -> Path:
    """保留自訂檔名，但使用實際內容所對應的正確副檔名。"""
    suffix_map = {"zip": ".zip", "csv": ".csv", "xls": ".xls"}
    suffix = suffix_map.get(detected)
    if suffix is None:
        suggested_suffix = Path(suggested_filename).suffix.lower()
        suffix = suggested_suffix or target.suffix or ".download"
    return target.with_suffix(suffix)


def export_invoices(
    page: Page,
    start: str,
    end: str,
    *,
    paper_only: bool,
    csv: bool,
    detail: bool,
    target: Path,
) -> Path:
    page.goto(EI_EXPORT_URL, wait_until="domcontentloaded")
    if page.locator("#userid").count() and page.locator("#userid").first.is_visible():
        raise RuntimeError("第二層登入已失效")
    page.locator("#f1").wait_for(state="visible", timeout=30_000)
    set_readonly_value(page, "#date1", to_roc_date(start))
    set_readonly_value(page, "#date2", to_roc_date(end))
    page.locator("#searchway3" if paper_only else "#searchway1").check(force=True)
    page.locator("#status0").check(force=True)
    page.locator("#export2" if csv else "#export1").check(force=True)
    page.locator("#hasdetail1" if detail else "#hasdetail0").check(force=True)
    page.locator("#hasroute0").check(force=True)
    page.locator("#exceltype0").check(force=True)
    for selector in ("#source", "#payway"):
        select = page.locator(selector)
        if select.count():
            # EI 使用 jQuery multiselect，原始 select 會被隱藏；直接設定
            # option.selected，避免 Playwright 等待不可見欄位而逾時。
            select.evaluate(
                "el => {"
                "  Array.from(el.options).forEach(option => {"
                "    option.selected = Boolean(option.value);"
                "  });"
                "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                "}"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=60_000) as info:
        page.get_by_text("直接匯出", exact=False).click()

    download = info.value
    suggested = download.suggested_filename or ""
    print(f"  網站建議檔名：{suggested or '(無)'}")

    # 先存入暫存檔，再依內容判斷 ZIP / CSV / Excel / 錯誤頁。
    with tempfile.TemporaryDirectory(prefix="ei_export_") as temp_dir:
        temp_path = Path(temp_dir) / (suggested or "ei_download.tmp")
        download.save_as(temp_path)
        detected = detect_download_format(temp_path)
        if detected == "unknown":
            raise RuntimeError(
                f"無法辨識 EI 下載格式；網站建議檔名={suggested or '(無)'}，"
                f"檔案大小={temp_path.stat().st_size} bytes"
            )

        final_path = final_download_path(target, detected, suggested)
        if final_path.exists():
            final_path.unlink()
        temp_path.replace(final_path)

    print(f"  實際格式：{detected.upper()}")
    print(f"  已匯出：{final_path}")
    return final_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用瀏覽器下載 EI 各區整月發票資料")
    parser.add_argument("month", nargs="?", help="年月 YYYYMM，例如 202606")
    parser.add_argument("--month", dest="month_option", help="相容舊指令：年月 YYYYMM")
    parser.add_argument("--area", help="單一區域，例如 台中；未指定即全部已設定區域")
    parser.add_argument("--accounts", type=Path, help="EI 帳密 JSON")
    parser.add_argument("--output-root", type=Path, help="自訂輸出根目錄")
    parser.add_argument("--format", choices=("csv", "excel"), default="csv")
    parser.add_argument("--detail", action="store_true", help="匯出含明細")
    parser.add_argument("--slow-mo", type=int, default=100, help="瀏覽器每步延遲毫秒")
    parser.add_argument(
        "--chrome-profile",
        type=Path,
        default=DEFAULT_CHROME_PROFILE,
        help=f"保留第一層登入狀態的 Chrome 設定檔（預設：{DEFAULT_CHROME_PROFILE}）",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="連接已開啟 Chrome 的控制網址（預設：http://127.0.0.1:9222）",
    )
    parser.add_argument(
        "--launch-chrome",
        action="store_true",
        help="略過 CDP 偵測，直接由程式使用指定 Chrome Profile 開啟視窗",
    )
    parser.add_argument(
        "--require-existing-chrome",
        action="store_true",
        help="只允許連接已用遠端除錯模式開啟的 Chrome；連不到時直接報錯",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    yyyymm = normalize_month(args.month_option or args.month)
    start, end = month_range(yyyymm)
    accounts = load_accounts(args.accounts)
    areas = [normalize_area(args.area)] if args.area and args.area.lower() != "all" else configured_areas(accounts)
    csv = args.format == "csv"
    extension = ".csv" if csv else ".xls"
    print(f"日期：{start} ～ {end}")
    print(f"區域：{', '.join(AREA_ENV[area]['label'] for area in areas)}")

    failures: list[str] = []
    with sync_playwright() as playwright:
        chrome_profile = args.chrome_profile.expanduser()
        chrome_profile.mkdir(parents=True, exist_ok=True)

        connected_browser = None
        owns_context = False
        context = None

        # 先嘗試接管已經用 remote-debugging-port 開啟的 Chrome。
        # 一般方式啟動的 Chrome 無法在啟動後再被 Playwright 接管。
        if not args.launch_chrome:
            try:
                connected_browser = playwright.chromium.connect_over_cdp(args.cdp_url)
                if connected_browser.contexts:
                    context = connected_browser.contexts[0]
                    print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
                else:
                    connected_browser.close()
                    connected_browser = None
            except Exception:
                connected_browser = None

        if context is None:
            if args.require_existing_chrome:
                raise RuntimeError(
                    f"找不到可連接的 Chrome：{args.cdp_url}\n"
                    "請先以 --remote-debugging-port=9222 啟動 Chrome，"
                    "或移除 --require-existing-chrome 讓程式自動開啟專用視窗。"
                )

            # 連不到既有 Chrome 時，自動用同一個 EI 專用 Profile 開啟。
            # 此 Profile 會保留 Cookie 與登入狀態，不需每次重新建立帳號。
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=chrome_profile,
                    channel="chrome",
                    headless=False,
                    slow_mo=args.slow_mo,
                    accept_downloads=True,
                    locale="zh-TW",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"無法開啟 EI 專用 Chrome Profile：{chrome_profile}\n"
                    "可能已有另一個 Chrome 正在使用同一個 Profile。"
                ) from exc
            owns_context = True
            print(f"瀏覽器：已自動開啟 EI 專用 Chrome Profile：{chrome_profile}")

        portal_page = next(
            (page for page in context.pages if "cetustek.com.tw" in page.url),
            None,
        ) or context.new_page()
        try:
            login_portal(portal_page, accounts)
            for index, area in enumerate(areas):
                credentials = credentials_for(area, accounts)
                print(f"\n開始處理：{credentials.label}")
                try:
                    if index and "cetustek.com.tw" not in portal_page.url:
                        portal_page = context.new_page()
                        portal_page.goto(PORTAL_MEMBER_URL, wait_until="domcontentloaded")
                    ei_page = open_second_login(context, portal_page)
                    login_second(ei_page, credentials)
                    area_dir = output_dir(area, accounts, yyyymm, args.output_root)
                    export_invoices(
                        ei_page,
                        start,
                        end,
                        paper_only=False,
                        csv=csv,
                        detail=args.detail,
                        target=area_dir / f"{yyyymm}-2發票-{credentials.label}{extension}",
                    )
                    export_invoices(
                        ei_page,
                        start,
                        end,
                        paper_only=True,
                        csv=csv,
                        detail=args.detail,
                        target=area_dir / f"{yyyymm}紙本發票-{credentials.label}{extension}",
                    )
                    logout_second(ei_page, credentials.label)
                    if ei_page is not portal_page:
                        ei_page.close()
                except Exception as exc:
                    failures.append(f"{credentials.label}: {exc}")
                    print(f"  失敗：{exc}", file=sys.stderr)
        finally:
            if failures:
                try:
                    input("\n發生錯誤，瀏覽器會保留供檢查。查看完成後按 Enter 關閉：")
                except EOFError:
                    pass
            if owns_context and context is not None:
                context.close()
            # 連接既有 Chrome 時只結束 Playwright 連線，不關閉使用者的 Chrome。

    if failures:
        print("\n未完成：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\n全部發票匯出完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
