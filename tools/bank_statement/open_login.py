from __future__ import annotations

import argparse
import fcntl
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Frame, Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, BankAccount, load_account
from tools.bank_statement.capture import copy_to_clipboard, find_fubon_statement
from tools.bank_statement.sheet_filter import read_and_filter


LOGIN_URLS = {
    "fubon": "https://ebank.taipeifubon.com.tw/B2C/common/Index.faces",
    "yuanta": "https://b2bank.yuantabank.com.tw/B2C/login/LOGIN_Home.faces",
}
DEFAULT_CHROME_PROFILE = Path.home() / "Bank account" / "chrome_profile"


def acquire_run_lock(bank: str, area: str) -> TextIO:
    """避免舊的銀行程序仍在背景控制同一個 Chrome。"""
    lock_path = Path.home() / "Bank account" / f"bank_statement_{bank}_{area}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            f"已有 {area} {bank} 銀行程式正在執行；請先在原終端機按 Control+C。"
        ) from exc
    return handle


def visible_context(page: Page, selector: str, timeout_ms: int = 30_000) -> Frame | Page:
    waited = 0
    while waited < timeout_ms:
        for context in [page, *page.frames]:
            locator = context.locator(selector)
            for index in range(locator.count()):
                if locator.nth(index).is_visible():
                    return context
        page.wait_for_timeout(250)
        waited += 250
    raise RuntimeError(f"找不到登入欄位：{selector}")


def visible_in_viewport(page: Page, selector: str, timeout_ms: int = 30_000) -> object:
    """取得真正位於畫面中的元素；排除輪播區中雖 visible 但在畫面外的複本。"""
    waited = 0
    while waited < timeout_ms:
        viewport = page.evaluate("() => ({width: innerWidth, height: innerHeight})")
        for context in [page, *page.frames]:
            locator = context.locator(selector)
            for index in range(locator.count()):
                item = locator.nth(index)
                if not item.is_visible():
                    continue
                box = item.bounding_box()
                if not box:
                    continue
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                if 0 <= center_x <= viewport["width"] and 0 <= center_y <= viewport["height"]:
                    return item
        page.wait_for_timeout(250)
        waited += 250
    raise RuntimeError(f"找不到畫面中的元素：{selector}")


def fill_fubon(page: Page, account: BankAccount) -> Page:
    browser_context = page.context
    header = visible_context(page, '[id="header_form:header_login"]')
    login_buttons = header.locator('[id="header_form:header_login"]')
    visible_login = next(
        (
            login_buttons.nth(index)
            for index in range(login_buttons.count())
            if login_buttons.nth(index).is_visible()
        ),
        None,
    )
    if visible_login is None:
        raise RuntimeError("找不到可見的富邦登入按鈕")
    visible_login.evaluate("el => el.click()")
    context: Frame | Page | None = None
    active_page = page
    fields = []
    for _ in range(40):
        pages = [item for item in browser_context.pages if not item.is_closed() and "taipeifubon" in item.url]
        for candidate_page in reversed(pages):
            for candidate in [candidate_page, *candidate_page.frames]:
                try:
                    body_text = candidate.locator("body").inner_text(timeout=500)
                    if "身分證字號" not in body_text or "使用者代碼" not in body_text:
                        continue
                    row_fields = []
                    for label in ("身分證字號", "使用者代碼", "使用者密碼", "請輸入右側驗證碼"):
                        label_node = candidate.get_by_text(label, exact=True).filter(visible=True).first
                        row_input = label_node.locator("xpath=ancestor::tr[1]//input[not(@type='hidden')]").first
                        if not row_input.count() or not row_input.is_visible():
                            break
                        row_fields.append(row_input)
                    if len(row_fields) == 4:
                        active_page, context, fields = candidate_page, candidate, row_fields
                        break
                    fallback_fields = candidate.locator(
                        'input:visible:not([type="hidden"]):not([type="button"]):not([type="submit"])'
                    )
                    if fallback_fields.count() >= 4:
                        active_page, context = candidate_page, candidate
                        fields = [fallback_fields.nth(index) for index in range(4)]
                        break
                except Exception:
                    continue
            if context is not None:
                break
        if context is not None:
            break
        time.sleep(0.25)
    if context is None:
        details = []
        for frame in page.frames:
            try:
                details.append(f"{frame.url}（可見輸入框 {frame.locator('input:visible').count()}）")
            except Exception:
                pass
        raise RuntimeError("富邦登入彈窗已開啟，但找不到四個登入欄位。頁框：\n" + "\n".join(details))

    identity, user_code, password, captcha = fields

    # 富邦彈窗開啟後會再初始化動態鍵盤；太早填寫會被清空。
    # 密碼欄另有安全鍵盤腳本，不能用 input_value 回讀判斷是否成功。
    active_page.wait_for_timeout(750)

    def type_like_user(locator: object, value: str) -> None:
        locator.click()
        locator.press("Meta+A")
        locator.press("Backspace")
        locator.type(value, delay=15)

    type_like_user(identity, account.login_id)
    type_like_user(user_code, account.user_code)
    type_like_user(password, account.password)
    captcha.focus()
    active_page.bring_to_front()
    return active_page

    # 不自動補填第二次；富邦安全鍵盤可能隱藏實際值，重填反而會造成重複輸入。


def fill_yuanta(page: Page, account: BankAccount) -> None:
    context = visible_context(page, "#login\\:viewCompanyUid")
    context.locator("#login\\:viewCompanyUid").fill(account.login_id)
    context.locator("#login\\:userUuid").fill(account.user_code)
    context.locator("#login\\:password").fill(account.password)
    context.locator("#login\\:pictCode").focus()


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式請用 YYYY-MM-DD") from exc


def visible_text(page: Page, text: str, timeout_ms: int = 30_000) -> tuple[Frame | Page, object]:
    waited = 0
    while waited < timeout_ms:
        for context in [page, *page.frames]:
            locator = context.get_by_text(text, exact=True)
            for index in range(locator.count()):
                item = locator.nth(index)
                if item.is_visible():
                    return context, item
        page.wait_for_timeout(250)
        waited += 250
    raise RuntimeError(f"找不到可點選項目：{text}")


def fubon_pages(context: BrowserContext) -> list[Page]:
    origin = LOGIN_URLS["fubon"].split("/B2C")[0]
    return [page for page in context.pages if not page.is_closed() and origin in page.url]


def current_fubon_page(context: BrowserContext, fallback: Page | None = None) -> Page | None:
    pages = fubon_pages(context)
    logged_in = []
    for candidate in pages:
        try:
            if is_fubon_logged_in(candidate):
                logged_in.append(candidate)
        except Exception:
            continue
    if logged_in:
        return logged_in[-1]
    if pages:
        return pages[-1]
    if fallback is not None and not fallback.is_closed():
        return fallback
    return None


def wait_fubon_page_with_text(
    context: BrowserContext,
    fallback: Page | None,
    texts: tuple[str, ...],
    timeout_ms: int = 30_000,
) -> Page:
    """等待富邦換頁完成，並回傳真正包含指定文字的新分頁。"""
    waited = 0
    while waited < timeout_ms:
        candidates = list(reversed(fubon_pages(context)))
        if fallback is not None and not fallback.is_closed() and fallback not in candidates:
            candidates.append(fallback)
        for candidate in candidates:
            try:
                if any(has_visible_text(candidate, text) for text in texts):
                    return candidate
            except Exception:
                continue
        time.sleep(0.25)
        waited += 250
    raise RuntimeError(f"富邦換頁後找不到：{'／'.join(texts)}")


def wait_fubon_login(
    context: BrowserContext, page: Page, timeout_ms: int = 30_000
) -> Page:
    """富邦登入可能關閉原分頁並開新分頁，因此每輪都重新鎖定有效頁面。"""
    waited = 0
    while waited < timeout_ms:
        active_page = current_fubon_page(context, page)
        if active_page is None:
            time.sleep(0.5)
            waited += 500
            continue
        page = active_page
        try:
            logged_in = is_fubon_logged_in(page)
        except Exception:
            # 分頁正於登入後替換；下一輪改抓新分頁。
            time.sleep(0.5)
            waited += 500
            continue
        if logged_in:
            # 一偵測到已登入便交給明細流程；首頁元件可能延遲載入，
            # 不應在此重複等待「我的存款」或快速功能。
            dismiss_fubon_idle_dialog(page)
            time.sleep(0.25)
            return current_fubon_page(context, page) or page
        time.sleep(0.5)
        waited += 500
    raise RuntimeError("等待富邦登入逾時")


def write_date_input(locator: object, value: date) -> None:
    text = value.strftime("%Y/%m/%d")
    locator.evaluate(
        """(el, value) => {
          el.removeAttribute('readonly');
          el.value = value;
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        text,
    )


def click_nearest_control(locator: object) -> None:
    """點擊文字所在的按鈕／連結／onclick 容器，避免只點到內層 span。"""
    controls = locator.locator(
        "xpath=ancestor-or-self::*[self::a or self::button or @onclick][1]"
    )
    if controls.count() and controls.first.is_visible():
        controls.first.evaluate("el => el.click()")
    else:
        locator.evaluate("el => el.click()")


def has_visible_text(page: Page, text: str) -> bool:
    for context in [page, *page.frames]:
        locator = context.get_by_text(text, exact=True)
        for index in range(locator.count()):
            if locator.nth(index).is_visible():
                return True
    return False


def has_visible_text_containing(page: Page, text: str) -> bool:
    """以部分文字辨識畫面；富邦會在日期、秒數等內容中插入動態文字。"""
    for context in [page, *page.frames]:
        try:
            locator = context.get_by_text(text, exact=False)
            for index in range(locator.count()):
                if locator.nth(index).is_visible():
                    return True
        except Exception:
            # 登入後富邦會替換 iframe；讀取到已卸載的 frame 時交給下一個判斷。
            continue
    return False


def is_fubon_logged_in(page: Page) -> bool:
    frame_urls = [frame.url for frame in page.frames]
    # 富邦未登入頁的共用頁首也會顯示「登出」，不能以該文字單獨判定。
    # 真正登入前會存在 PreLogin.faces；部分版本登入後仍保留舊 PreLogin frame，
    # 因此要先檢查登入後的正向訊號，不能看見 PreLogin 就立即回傳 False。
    if any("/Logout.faces" in url for url in frame_urls):
        return True
    # 2026-08 富邦登入後仍停在 /common/Index.faces，功能 iframe 也可能全在
    # /common/ 下。首頁底部的「上次登入時間」只會出現在有效登入工作階段。
    if has_visible_text_containing(page, "您上次登入系統時間為"):
        return True
    # 已進入帳號清單或交易明細查詢時也視為登入成功。
    if query_form_context(page) is not None:
        return True
    if has_visible_text(page, "快速功能") and has_visible_text(page, "交易明細查詢"):
        return True
    if any("/PreLogin.faces" in url for url in frame_urls):
        return False
    # 登入後主要功能會載入 /B2C/<功能代碼>/... 頁框；登入前僅有 /common/。
    return any(
        "/B2C/" in url and "/B2C/common/" not in url
        for url in frame_urls
        if url.startswith("http")
    )


def dismiss_fubon_idle_dialog(page: Page) -> bool:
    """關閉富邦「閒置過久」提示，避免遮住後續功能操作。"""
    for context in [page, *page.frames]:
        try:
            buttons = context.get_by_text("繼續使用", exact=True)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if not button.is_visible():
                    continue
                click_nearest_control(button)
                page.wait_for_timeout(250)
                print("已關閉富邦閒置提示，繼續執行。")
                return True
        except Exception:
            continue
    return False


def wait_visible_text(page: Page, text: str, timeout_ms: int = 2_000) -> bool:
    waited = 0
    while waited < timeout_ms:
        if has_visible_text(page, text):
            return True
        page.wait_for_timeout(100)
        waited += 100
    return False


def real_mouse_click(page: Page, locator: object) -> None:
    locator.scroll_into_view_if_needed(timeout=2_000)
    box = locator.bounding_box()
    if not box:
        raise RuntimeError("找不到快速功能的畫面座標")
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=12)
    page.wait_for_timeout(350)
    page.mouse.click(x, y)


def real_mouse_click_tile(page: Page, locator: object) -> None:
    """用 Playwright 的全頁座標點功能方塊，正確計入 iframe 偏移。"""
    locator.scroll_into_view_if_needed(timeout=2_000)
    box = locator.bounding_box()
    if not box:
        return real_mouse_click(page, locator)
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=12)
    page.wait_for_timeout(350)
    page.mouse.click(x, y)


def real_mouse_click_right_arrow(page: Page, locator: object) -> None:
    """點擊快速功能控制項右側的黑色下拉箭頭。"""
    locator.scroll_into_view_if_needed(timeout=2_000)
    box = locator.bounding_box()
    if not box:
        raise RuntimeError("找不到快速功能欄位的畫面座標")
    x = box["x"] + box["width"] - min(24, box["width"] / 5)
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=12)
    page.wait_for_timeout(350)
    page.mouse.click(x, y)


def open_account_quick_menu(page: Page, account_row: object) -> None:
    quick = account_row.get_by_text("快速功能", exact=True)
    visible = [quick.nth(index) for index in range(quick.count()) if quick.nth(index).is_visible()]
    if not visible:
        raise RuntimeError("指定帳號列找不到快速功能")

    # 富邦目前版本在文字／外層 onclick 收到滑鼠事件後會直接開啟選單。
    # 先走實際畫面上驗證成功的操作，再保留右側箭頭與 select 作為相容 fallback。
    for item in visible:
        try:
            click_nearest_control(item)
            if wait_visible_text(page, "交易明細查詢", timeout_ms=1_500):
                return
        except Exception:
            continue

    # 文字中央只會取得藍色焦點；實際選單由控制項右側箭頭開啟。
    cells = account_row.locator("td")
    if cells.count():
        function_cell = cells.nth(cells.count() - 1)
        try:
            controls = function_cell.locator("select, button, input, [role='button'], [onclick]")
            control = next(
                (controls.nth(index) for index in range(controls.count()) if controls.nth(index).is_visible()),
                function_cell,
            )

            # 部分版本把快速功能做成原生 select；直接選 option 比滑鼠點箭頭穩定。
            if control.evaluate("el => el.tagName") == "SELECT":
                options = control.locator("option")
                for index in range(options.count()):
                    option = options.nth(index)
                    if "交易明細查詢" in option.inner_text():
                        control.select_option(option.get_attribute("value") or "")
                        if wait_visible_text(page, "交易明細查詢", timeout_ms=3_000):
                            return

            real_mouse_click_right_arrow(page, control)
            if wait_visible_text(page, "交易明細查詢", timeout_ms=3_000):
                return
        except Exception as exc:
            print(f"快速功能操作未完成：{exc}")

        # 只保存功能欄本身，不包含帳號與餘額；供下一次失敗時直接診斷。
        try:
            debug_file = Path("/tmp/fubon_quick_function.html")
            debug_file.write_text(function_cell.evaluate("el => el.outerHTML"), encoding="utf-8")
            print(f"快速功能診斷已保存：{debug_file}")
        except Exception:
            pass
    raise RuntimeError("已找到指定帳號，但無法開啟該列的快速功能選單")


def find_account_row(page: Page, account: BankAccount, timeout_ms: int = 5_000) -> object | None:
    account_digits = re.sub(r"\D", "", account.bank_account)
    suffix = account_digits[-5:]
    waited = 0
    while waited < timeout_ms:
        matches: list[tuple[int, int, object]] = []
        for candidate in [page, *page.frames]:
            rows = candidate.locator("tr").filter(has_text=suffix)
            for index in range(rows.count()):
                row = rows.nth(index)
                row_digits = re.sub(r"\D", "", row.inner_text())
                if not row.is_visible() or suffix not in row_digits:
                    continue
                quick = row.get_by_text("快速功能", exact=True)
                visible_quick_count = sum(
                    quick.nth(item).is_visible() for item in range(quick.count())
                )
                if visible_quick_count:
                    matches.append((visible_quick_count, len(row.inner_text()), row))
        if matches:
            return min(matches, key=lambda item: (item[0], item[1]))[2]
        page.wait_for_timeout(250)
        waited += 250
    return None


def query_form_context(page: Page) -> tuple[Frame | Page, object, object] | None:
    for candidate in [page, *page.frames]:
        start = candidate.locator('[id="form1:startDate"]')
        end = candidate.locator('[id="form1:endDate"]')
        if start.count() and end.count() and start.first.is_visible() and end.first.is_visible():
            return candidate, start.first, end.first
    return None


def wait_fubon_query_form(
    browser_context: BrowserContext,
    page: Page,
    timeout_ms: int = 20_000,
) -> tuple[Page, Frame | Page, object, object]:
    waited = 0
    while waited < timeout_ms:
        page = current_fubon_page(browser_context, page) or page
        if not page.is_closed():
            found = query_form_context(page)
            if found:
                context, start, end = found
                return page, context, start, end
        time.sleep(0.25)
        waited += 250
    raise RuntimeError("富邦未能進入交易明細查詢頁")


def open_fubon_statement(
    browser_context: BrowserContext,
    page: Page,
    account: BankAccount,
    start: date,
    end: date,
) -> Page:
    if not account.bank_account:
        raise RuntimeError("bank_accounts.json 尚未設定 bank_account，無法選擇查詢帳號")

    dismiss_fubon_idle_dialog(page)

    query_form = query_form_context(page)
    if query_form is None:
        account_row = find_account_row(page, account, timeout_ms=2_000)
    else:
        account_row = None

    # 尚未在帳號清單時，從首頁點「我的存款」。
    if query_form is None and account_row is None:
        print("步驟 1/4：點擊『我的存款』。")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            # 首頁同時存在多個輪播複本；選取真正位於視窗中的連結。
            tile = visible_in_viewport(
                page, 'a:has-text("我的存款")', timeout_ms=8_000
            )
            tile.evaluate("el => el.click()")
        except Exception as text_error:
            # 舊版首頁 class 作為相容備援。
            try:
                tile = visible_in_viewport(
                    page, "a.task_CBOQU003", timeout_ms=2_000
                )
                tile.evaluate("el => el.click()")
            except Exception as selector_error:
                raise RuntimeError(
                    f"找不到可點擊的『我的存款』（文字：{text_error}；選擇器：{selector_error}）"
                ) from selector_error

        page = wait_fubon_page_with_text(
            browser_context, page, ("快速功能",), timeout_ms=20_000
        )
        print("已進入『我的存款』帳號清單。")
        account_row = find_account_row(page, account, timeout_ms=10_000)

    if query_form is None:
        if account_row is None:
            raise RuntimeError(f"我的存款頁找不到帳號末碼 {account.bank_account[-5:]}")
        print(f"步驟 2/4：帳號末碼 {account.bank_account[-5:]}，開啟『快速功能』。")
        open_account_quick_menu(page, account_row)
        _, statement_query = visible_text(page, "交易明細查詢", timeout_ms=5_000)
        click_nearest_control(statement_query)
        print("步驟 3/4：點擊『交易明細查詢』。")
        page, context, start_input, end_input = wait_fubon_query_form(browser_context, page)
    else:
        context, start_input, end_input = query_form

    custom = context.locator('[id="form1:rdoCustom"]')
    if custom.count():
        # 富邦以 CSS 隱藏原生 radio；直接觸發 DOM click 才會執行 onclick。
        custom.first.evaluate("el => el.click()")
    write_date_input(start_input, start)
    write_date_input(end_input, end)

    if account.bank_account:
        selects = context.locator("select")
        for index in range(selects.count()):
            select = selects.nth(index)
            if not select.is_visible():
                continue
            options = select.locator("option")
            for option_index in range(options.count()):
                option = options.nth(option_index)
                if account.bank_account[-5:] in option.inner_text():
                    select.select_option(option.get_attribute("value") or "")
                    break

    _, query = visible_text(page, "開始查詢", timeout_ms=5_000)
    click_nearest_control(query)
    print(f"步驟 4/4：查詢日期 {start:%Y/%m/%d}～{end:%Y/%m/%d}。")
    return current_fubon_page(browser_context, page) or page


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="開啟銀行登入頁並從本機 JSON 預填帳密")
    parser.add_argument("--bank", required=True, choices=("fubon", "yuanta"), help="fubon=富邦；yuanta=元大")
    parser.add_argument("--area", required=True, choices=("台北", "台中", "桃園", "新竹", "高雄"))
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--slow-mo", type=int, default=100)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    parser.add_argument("--launch-chrome", action="store_true", help="由程式開啟可保留狀態的 Google Chrome")
    parser.add_argument("--chrome-profile", type=Path, default=DEFAULT_CHROME_PROFILE)
    parser.add_argument("--start", type=parse_date, default=date.today(), help="查詢起日，YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=date.today(), help="查詢迄日，YYYY-MM-DD")
    return parser.parse_args()


def keep_chrome_open(page: Page, bank: str, area: str) -> None:
    if bank == "fubon":
        print("交易明細表出現後，程式會自動比對 Google Sheet 並複製新增列。")
    print("銀行 Chrome 會持續保持開啟；完成作業後按 Control+C 結束程式。")
    captured_fingerprint = None
    try:
        while not page.is_closed():
            if bank == "fubon":
                table = find_fubon_statement(page)
                if table is not None and table.fingerprint != captured_fingerprint:
                    captured_fingerprint = table.fingerprint
                    try:
                        new_table = read_and_filter(table, area, bank)
                        if new_table.rows:
                            copy_to_clipboard(new_table)
                            print(
                                f"共擷取 {len(table.rows)} 筆；排除已同步資料後，"
                                f"有 {len(new_table.rows)} 筆新增明細已複製。"
                                "請到報表下一個空白列的 B 欄貼上。"
                            )
                        else:
                            print(f"共擷取 {len(table.rows)} 筆；Google Sheet 已全部存在，無新增資料。")
                    except Exception as exc:
                        print(f"Google Sheet 比對失敗，為避免重複，這次不複製：{exc}")
            page.wait_for_timeout(1_000)
    except KeyboardInterrupt:
        print("\n已停止銀行瀏覽器工作階段。")


def main() -> int:
    args = parse_args()
    run_lock = acquire_run_lock(args.bank, args.area)
    account = load_account(args.bank, args.area, args.accounts_file.expanduser())
    bank_name = "富邦" if args.bank == "fubon" else "元大"
    print(f"銀行：{bank_name}｜區域：{account.area}")
    if account.bank_account:
        print(f"查詢帳戶：***{account.bank_account[-5:]}")

    with sync_playwright() as playwright:
        owns_context = args.launch_chrome
        connected_browser = None
        if owns_context:
            args.chrome_profile.expanduser().mkdir(parents=True, exist_ok=True)
            try:
                context: BrowserContext = playwright.chromium.launch_persistent_context(
                    user_data_dir=args.chrome_profile.expanduser(),
                    channel="chrome",
                    headless=False,
                    slow_mo=args.slow_mo,
                    locale="zh-TW",
                )
            except PlaywrightError as exc:
                if "ProcessSingleton" in str(exc) or "profile is already in use" in str(exc):
                    raise RuntimeError(
                        "銀行專用 Chrome 尚未關閉。請只關閉使用 "
                        f"{args.chrome_profile.expanduser()} 的 Chrome，再重新執行。"
                    ) from exc
                raise
        else:
            try:
                connected_browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            except Exception as exc:
                raise RuntimeError(
                    "找不到可連接的 Chrome。請先執行：\n"
                    'open -na "Google Chrome" --args --remote-debugging-port=9223 '
                    '--user-data-dir="$HOME/Bank account/chrome_profile"'
                ) from exc
            if not connected_browser.contexts:
                raise RuntimeError("Chrome 已連接，但找不到可用視窗")
            context = connected_browser.contexts[0]
        bank_pages = [
            item for item in context.pages if LOGIN_URLS[args.bank].split("/B2C")[0] in item.url
        ]
        # 多個銀行分頁並存時，優先沿用真正顯示「登出」的有效工作階段。
        page = next((item for item in reversed(bank_pages) if has_visible_text(item, "登出")), None)
        page = page or (bank_pages[-1] if bank_pages else None)
        page = page or context.new_page()
        try:
            # 保留已登入的銀行頁面，避免再次導向登入頁而中斷既有工作階段。
            already_logged_in = (
                args.bank == "fubon"
                and has_visible_text(page, "登出")
            )
            if already_logged_in:
                print("偵測到富邦已登入，沿用目前工作階段。")
            else:
                page.goto(LOGIN_URLS[args.bank], wait_until="domcontentloaded", timeout=60_000)
                if args.bank == "fubon":
                    page = fill_fubon(page, account)
                else:
                    fill_yuanta(page, account)
                print("帳密已預填，請在 Chrome 輸入圖形驗證碼並登入。")
            if args.bank == "fubon":
                try:
                    page = wait_fubon_login(context, page)
                    print("富邦已穩定登入，開始開啟指定帳號的交易明細查詢。")
                    page = open_fubon_statement(context, page, account, args.start, args.end)
                except Exception as exc:
                    print(f"富邦自動開啟明細失敗：{exc}")
                    try:
                        page = wait_fubon_page_with_text(
                            context,
                            page,
                            ("自訂查詢", "查詢期間", "快速功能", "我的存款"),
                            timeout_ms=10_000,
                        )
                    except Exception:
                        page = current_fubon_page(context, page) or page
            keep_chrome_open(page, args.bank, args.area)
        except Exception as exc:
            print(f"登入頁處理失敗：{exc}")
            keep_chrome_open(page, args.bank, args.area)
            raise
        finally:
            if owns_context:
                context.close()
            run_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
