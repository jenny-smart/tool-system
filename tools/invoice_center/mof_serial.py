# 財政部電子發票整合服務平台（einvoice.nat.gov.tw）
# 「電子發票字軌號碼取號」半自動下載＋歸檔。
#
# 登入頁有圖形驗證碼，一律留給使用者在畫面上手動輸入密碼與驗證碼、按「登入」；
# 本程式只負責預填統一編號／帳號，以及登入後的選單導覽、查詢、下載、
# 歸檔到 Google Drive「紙本發票／期別」資料夾（跟鯨躍紙本發票共用同一套
# 地區設定與資料夾規則），並寫入「鯨躍字軌匯入配號Log」（跟鯨躍字軌匯入／
# 配號共用同一份執行記錄分頁，見 invoice_archive.write_serial_log）。
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center.mof_config import (
        MOF_LOGIN_URL,
        MOFCredentials,
        configured_areas,
        credentials_for,
        load_accounts,
    )
    from invoice_center.invoice_archive import (
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
        upload_replacing,
        write_serial_log,
        _master_spreadsheet_id,
    )
else:
    from .mof_config import (
        MOF_LOGIN_URL,
        MOFCredentials,
        configured_areas,
        credentials_for,
        load_accounts,
    )
    from .invoice_archive import (
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
        upload_replacing,
        write_serial_log,
        _master_spreadsheet_id,
    )

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)

MOF_SERIAL_QUERY_URL = "https://www.einvoice.nat.gov.tw/dashboard/btb/btb004w/search"
MOF_LOG_FUNCTION_NAME = "財政部電子發票取號"

_PERIOD_LABELS = ["01-02", "03-04", "05-06", "07-08", "09-10", "11-12"]


def period_of(d: date) -> tuple[int, str]:
    return d.year, _PERIOD_LABELS[(d.month - 1) // 2]


def next_period(year: int, label: str) -> tuple[int, str]:
    idx = _PERIOD_LABELS.index(label)
    if idx == len(_PERIOD_LABELS) - 1:
        return year + 1, _PERIOD_LABELS[0]
    return year, _PERIOD_LABELS[idx + 1]


def parse_period_arg(period_text: str) -> tuple[int, str]:
    """接受 YYYYMM（起始月，例如 202609）或 YYYY年MM-MM期 格式；未輸入時預設下一期。"""
    text = str(period_text or "").strip()
    if not text:
        today = date.today()
        year, label = period_of(today)
        return next_period(year, label)
    match = re.fullmatch(r"(\d{4})(\d{2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        label = _PERIOD_LABELS[(month - 1) // 2]
        return year, label
    match = re.fullmatch(r"(\d{4})[年-](\d{2}-\d{2})期?", text)
    if match:
        return int(match.group(1)), match.group(2)
    raise ValueError(f"看不懂的期別格式：{period_text}，請用 YYYYMM，例如 202609")


def period_filename(year: int, label: str, area: str) -> str:
    start_mm, end_mm = label.split("-")
    return f"{year}{start_mm}{end_mm}電子發票取號-{area}.csv"


def find_mof_page(context) -> Page | None:
    return find_existing_page(context, ("einvoice.nat.gov.tw",))


def fill_if_present(page: Page, selector: str, value: str) -> bool:
    locator = page.locator(selector)
    if value and locator.count() and locator.first.is_visible():
        locator.first.fill(value)
        return True
    return False


MOF_LOGOUT_PATH = "/act/login/api/logoutClient/logout"
MOF_LOGOUT_URL = f"https://service-mc.einvoice.nat.gov.tw{MOF_LOGOUT_PATH}"


def logged_in_account(page: Page) -> str:
    """已登入畫面左上角「帳號：42627791」的帳號；讀不到回傳空字串。"""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""
    match = re.search(r"帳號\s*[:：]\s*([0-9A-Za-z]+)", text)
    return match.group(1) if match else ""


def is_expected_account(account: str, credentials: MOFCredentials) -> bool:
    return bool(account) and account in {credentials.ubn.strip(), credentials.account.strip()} - {""}


def logout_mof(page: Page) -> None:
    """按左下角「登出」（logoutClient/logout），再確認畫面已沒有登入帳號；登不出去就報錯（避免用錯的帳號繼續下載）。"""
    page.once("dialog", lambda dialog: dialog.accept())
    try:
        page.locator(f'a[href*="{MOF_LOGOUT_PATH}"], a[title="登出"]').first.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        # 找不到按鈕（例如不在後台頁）就直接開登出網址
        page.goto(MOF_LOGOUT_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    page.goto(MOF_LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    account = logged_in_account(page)
    if account:
        raise RuntimeError(f"登出後仍是登入狀態（帳號：{account}），請手動登出財政部電子發票平台後再執行；目前頁面：{page.url}")
    print("[財政部電子發票] 已登出")


def ensure_expected_login(page: Page, credentials: MOFCredentials) -> None:
    """確認目前登入的是這個地區的帳號；不是就報錯，絕不用別區帳號下載或存檔。"""
    account = logged_in_account(page)
    if not is_expected_account(account, credentials):
        raise RuntimeError(
            f"目前登入的帳號（{account or '讀不到'}）不是 {credentials.area} 的帳號（{credentials.masked_ubn}），"
            "已停止，避免把別區的字軌存成這一區"
        )


def login_mof(page: Page, credentials: MOFCredentials) -> None:
    """開啟登入頁並預填統一編號／帳號（與密碼，若帳密檔內有設定）。
    驗證碼與送出登入一律留給使用者手動操作。"""
    page.goto(MOF_LOGIN_URL, wait_until="domcontentloaded")

    # The SPA may first render /accounts/login and redirect to the dashboard a few
    # seconds later when an existing session is restored. Wait briefly for that
    # asynchronous redirect before interacting with the login form.
    redirect_deadline = time.monotonic() + 5
    while time.monotonic() < redirect_deadline and "/accounts/login" in page.url:
        page.wait_for_timeout(250)

    # session 還有效時，網站會直接把 /accounts/login 轉走到已登入的
    # dashboard，這裡就不用再走身分別／欄位那一整套（dashboard 上本來就
    # 沒有這些元素，硬找只會逾時報錯）。
    if "/accounts/login" not in page.url:
        account = logged_in_account(page)
        if is_expected_account(account, credentials):
            print(f"[財政部電子發票] 沿用目前登入狀態（{credentials.area}，帳號 {account}）")
            return
        # 已登入的是別區帳號（或讀不到帳號）：先登出再用這一區的帳號重新登入
        print(f"[財政部電子發票] 目前登入的帳號（{account or '讀不到'}）不是 {credentials.area}，先登出再重新登入")
        logout_mof(page)
        page.goto(MOF_LOGIN_URL, wait_until="domcontentloaded")

    # 這是前端 SPA：domcontentloaded 只代表原始 HTML 載完，畫面（含身分別
    # 圖示）是等 JS 執行完才動態渲染出來的。不能像傳統網頁那樣馬上用
    # count() 判斷元素在不在——這個時間點幾乎一定是 0，導致整段判斷被跳過、
    # 完全沒真的點到東西。改成直接 click()，交給 Playwright 的內建
    # auto-wait 等元素真正出現＋可點擊；真的等不到才視為失敗。
    #
    # 登入頁預設身分別不保證是「營業人/扣繳單位」（實測會落在消費者/手機
    # 條碼），一定要先切過去，並且「確認真的切成功」（統一編號欄位真的
    # 出現）才繼續，不然會誤填到手機條碼登入表單的欄位。
    identity_clicked = False
    for label in ("扣繳單位", "營業人"):
        try:
            page.get_by_text(label, exact=False).first.click(timeout=10_000)
            identity_clicked = True
            break
        except Exception:
            continue
    if not identity_clicked:
        raise RuntimeError(f"畫面上找不到可點擊的「營業人/扣繳單位」身分別；目前頁面：{page.url}")

    ubn_input = page.locator("input[name='LoginUBN'], input[placeholder*='統一編號']")
    try:
        ubn_input.first.wait_for(state="visible", timeout=10_000)
    except Exception:
        # A valid session can finish redirecting while we are waiting for the
        # login form. Treat that as success instead of reporting a missing field.
        if "/accounts/login" not in page.url:
            ensure_expected_login(page, credentials)
            print(f"[財政部電子發票] 沿用目前登入狀態（{page.url}）")
            return
        raise RuntimeError(
            "已點擊「營業人/扣繳單位」身分別，但還是找不到統一編號欄位；"
            f"目前頁面：{page.url}。可能是欄位結構跟預期不同，請截圖回報以便調整。"
        )

    # 身分別切成功後，登入方式預設就是「帳號」，這裡點擊只是保險，萬一之前
    # 被切到「憑證」分頁；找不到就算了（可能本來就已經是「帳號」）。
    try:
        page.get_by_text("帳號", exact=True).first.click(timeout=3000)
    except Exception:
        pass

    filled_ubn = fill_if_present(page, "input[name='LoginUBN'], input[placeholder*='統一編號']", credentials.ubn)
    filled_account = fill_if_present(page, "input[name='LoginAccount'], input[placeholder*='帳號']", credentials.account)
    if not filled_ubn and not filled_account:
        print("[財政部電子發票] 找不到統一編號／帳號欄位，請直接在網頁手動輸入。", file=sys.stderr)

    if credentials.password:
        fill_if_present(page, "input[type='password'], input[placeholder*='密碼']", credentials.password)
        print(f"[財政部電子發票] {credentials.area} 帳密已預填，請在網頁輸入圖形驗證碼並點擊「登入」。")
    else:
        print("[財政部電子發票] 帳密檔未設定密碼，請在網頁手動輸入密碼與圖形驗證碼並點擊「登入」。")

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if "/accounts/login" not in page.url:
            page.wait_for_timeout(2000)  # 等左上角帳號資訊畫出來
            ensure_expected_login(page, credentials)
            print(f"[財政部電子發票] {credentials.area} 登入成功")
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(f"登入等待逾時（5 分鐘），目前頁面：{page.url}")


def _open_serial_query(page: Page, year: int, label: str) -> None:
    # 實測這是 Vue SPA，左側選單要點兩次才會進到「電子發票字軌號碼取號」，
    # 「查詢」頁籤（<a title="查詢">）再點一次才會顯示查詢表單——一路模擬
    # 點擊很容易因為渲染時機踩雷。改成直接導到查詢頁的網址（跟畫面上
    # 手動導覽後看到的網址一樣），簡單也穩定很多；已登入時 Vue Router
    # 支援直接深連結進來。
    page.goto(MOF_SERIAL_QUERY_URL, wait_until="domcontentloaded")
    if "btb004w" not in page.url:
        raise RuntimeError(f"導到「電子發票字軌號碼取號／查詢」頁失敗，目前頁面：{page.url}（可能尚未登入）")

    # 實測畫面預設的發票期別範圍就是「這期～下一期」，剛好涵蓋我們平常
    # 要抓的目標期別，所以不去動期別選擇器（那是目前唯一沒拿到真實 HTML、
    # 純猜測的部分，風險最高）；直接按查詢，抓不到目標期別那一列再由
    # download_serial_csv() 丟出清楚錯誤，請人手動調整範圍。
    # 跟左側選單一樣，查詢按鈕實測要點兩次才會真的送出查詢（第一次疑似只
    # 是確認/套用目前的期別範圍）。
    for _ in range(2):
        try:
            page.locator("button[title='查詢']").first.click(timeout=10_000)
        except Exception:
            raise RuntimeError(f"找不到「查詢」按鈕；目前頁面：{page.url}")
        page.wait_for_timeout(1500)


# 找畫面上看得到、文字剛好是「是」的元素（不限 button 標籤），回傳候選數與看得到的數量
_FIND_CONFIRM_JS = """() => {
    const all = [...document.querySelectorAll('button, a, [role=button], .btn, span, div')]
        .filter(el => (el.innerText || el.textContent || '').replace(/\\s/g, '') === '是');
    const shown = all.filter(el => {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    });
    // 只要最內層的那個（避免點到包住按鈕的外框）
    const leaf = shown.filter(el => !shown.some(o => o !== el && el.contains(o)));
    leaf.forEach((el, i) => el.setAttribute('data-mof-confirm', String(i)));
    return [all.length, leaf.length];
}"""


def _wait_visible_confirm(page: Page, timeout_seconds: float) -> tuple[bool, int, int]:
    """等看得到的「是」出現；找到時已標上 data-mof-confirm='0'。回傳（是否找到、候選數、看得到數）。"""
    deadline = time.monotonic() + timeout_seconds
    total = shown = 0
    while True:
        total, shown = page.evaluate(_FIND_CONFIRM_JS)
        if shown:
            return True, total, shown
        if time.monotonic() >= deadline:
            return False, total, shown
        page.wait_for_timeout(300)


def _click_visible_confirm(page: Page, timeout_seconds: float = 15) -> None:
    """按下「下載」後的「注意事項」燈箱只有一個「是」；頁面上另有多份隱藏的同樣燈箱，只點看得到的。"""
    found, total, shown = _wait_visible_confirm(page, timeout_seconds)
    if not found:
        raise RuntimeError(
            f"按下「下載」後沒有出現確認燈箱：頁面上文字為「是」的元素 {total} 個，看得到的 {shown} 個；目前頁面：{page.url}"
        )
    page.locator("[data-mof-confirm='0']").first.click(timeout=8000)


def _click_download_button(page: Page) -> None:
    """查詢結果下方 card-footer 的「下載」按鈕（<button title="下載" class="btn btn-sm btn-falcon">）。"""
    for selector in (
        ".card-footer .btn_function button[title='下載']:visible",
        "button.btn-falcon[title='下載']:visible",
    ):
        button = page.locator(selector).filter(has_text="下載")
        if button.count():
            try:
                button.first.scroll_into_view_if_needed(timeout=5000)
                button.first.click(timeout=5000)
            except Exception:
                # 左側選單浮層擋住時，直接觸發按鈕本身的 click（Vue 的 @click 一樣會執行）
                button.first.evaluate("el => el.click()")
            return
    raise RuntimeError(f"找不到查詢結果下方的「下載」按鈕；目前頁面：{page.url}")


def download_serial_csv(page: Page, year: int, label: str, destination: Path) -> Path:
    _open_serial_query(page, year, label)

    period_text = f"{year}年{label}期"
    row = page.get_by_text(period_text, exact=False).locator("xpath=ancestor::tr[1]")
    try:
        row.first.wait_for(state="visible", timeout=10_000)
    except Exception:
        raise RuntimeError(f"查詢結果找不到「{period_text}」這一列，可能尚未取號或期別不存在；目前頁面：{page.url}")

    # 左側選單是固定浮層，偶爾會蓋到表格內容，導致 Playwright 判定「被其他
    # 元素擋住」而不敢點；這裡已經用 row 精準鎖定目標，用 force=True 直接
    # 對著座標點擊，忽略浮層攔截判定。
    checkbox = row.first.locator("input[type='checkbox']")
    if checkbox.count():
        target = checkbox.first
        if not target.is_checked():
            checkbox_id = target.get_attribute("id") or ""
            label_locator = page.locator(f'label[for="{checkbox_id}"]') if checkbox_id else None
            if label_locator is not None and label_locator.count():
                try:
                    label_locator.first.click(timeout=8000, force=True)
                except Exception:
                    pass
        if not target.is_checked():
            try:
                row.first.click(timeout=8000, force=True)
            except Exception:
                pass
        if not target.is_checked():
            # Vue-controlled checkboxes may immediately undo Playwright's synthetic click.
            # Use the native checked setter and emit both events so v-model updates too.
            target.evaluate(
                """element => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, "checked"
                    ).set;
                    setter.call(element, true);
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                }"""
            )
            page.wait_for_timeout(250)
        if not target.is_checked():
            raise RuntimeError(f"無法勾選「{period_text}」；請確認財政部頁面欄位是否改版")
    else:
        row.first.click(timeout=8000, force=True)

    try:
        with page.expect_download(timeout=60_000) as download_info:
            # 按下「下載」才會跳「注意事項：下載檔案編碼為UTF-8」燈箱；沒跳出來表示沒按到，最多再按兩次
            for attempt in range(3):
                _click_download_button(page)
                found, total, shown = _wait_visible_confirm(page, 6)
                if found:
                    page.locator("[data-mof-confirm='0']").first.click(timeout=8000)
                    break
                print(f"[財政部電子發票] 按「下載」後沒出現確認燈箱，重按（第 {attempt + 1} 次）")
            else:
                raise RuntimeError(
                    f"按了 3 次「下載」都沒有出現確認燈箱：文字為「是」的元素 {total} 個，看得到的 {shown} 個；目前頁面：{page.url}"
                )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"按下「是」後 60 秒內沒有開始下載；目前頁面：{page.url}") from e
    download = download_info.value
    destination.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(destination))
    return destination


def download_area_serial(page: Page, credentials: MOFCredentials, year: int, label: str) -> Path:
    """財政部字軌下載的唯一共用流程：確認是這區帳號 → 下載 → 一律登出（成功或失敗都登出）。"""
    destination = Path.home() / "MOF account" / "downloads" / period_filename(year, label, credentials.area)
    try:
        ensure_expected_login(page, credentials)  # 下載前再確認一次是這一區的帳號
        download_serial_csv(page, year, label, destination)
        print(f"[財政部電子發票] 已下載：{destination}")
        return destination
    finally:
        # 每次下載完都登出，下一區（或下次）執行時一定重新用正確帳號登入
        try:
            logout_mof(page)
        except Exception as exc:
            print(f"[財政部電子發票] ⚠️ 自動登出失敗，下次執行前請手動登出：{exc}", file=sys.stderr)


def archive_and_log(local_path: Path, area: str, year: int, label: str) -> str:
    period_text = f"{year}年{label}期"
    drive, sheets = get_google_services()
    spreadsheet_id = _master_spreadsheet_id()
    configs = ensure_config_sheet(sheets, spreadsheet_id)
    config = configs.get(area)
    if not config:
        message = f"「鯨躍發票根目錄設定」找不到已啟用的 {area} 設定"
        write_serial_log(sheets, spreadsheet_id, function_name=MOF_LOG_FUNCTION_NAME, area=area, period=period_text, status="失敗", message=message)
        raise RuntimeError(message)

    start_month = int(label.split("-")[0])
    yyyymm = f"{year}{start_month:02d}"
    folders = resolve_archive_folders(drive, config, yyyymm)
    upload_replacing(drive, local_path, folders.paper_invoice)

    message = f"已存為 {local_path.name}"
    write_serial_log(sheets, spreadsheet_id, function_name=MOF_LOG_FUNCTION_NAME, area=area, period=period_text, status="成功", message=message)
    return message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="財政部電子發票字軌號碼取號：查詢、下載、歸檔")
    parser.add_argument("--area", required=True, help="區域，例如：台北、台中")
    parser.add_argument("--period", default="", help="期別，格式 YYYYMM（起始月），例如 202609；未輸入時預設下一期")
    parser.add_argument("--accounts", type=Path, help="財政部電子發票帳密 JSON")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--login-only", action="store_true", help="只預填登入欄位，不下載")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = load_accounts(args.accounts)
    area = args.area or configured_areas(accounts)[0]
    credentials = credentials_for(area, accounts)
    year, label = parse_period_arg(args.period)

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
        page = find_mof_page(context)
        if page is None:
            page = context.new_page()
        # 一律導到登入頁：session 還有效的話網站本身會自動轉回登入後的頁面，
        # login_mof() 的輪詢會偵測到網址離開 /accounts/login 而視為登入成功；
        # 不在這裡自行判斷「已經登入」，避免誤判剛開出來的空白分頁而整個跳過導覽。
        login_mof(page, credentials)

        if args.login_only:
            return 0

        destination = download_area_serial(page, credentials, year, label)
        print(archive_and_log(destination, area, year, label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
