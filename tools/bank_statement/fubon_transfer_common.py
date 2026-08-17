"""富邦臺幣轉帳共用元件：立即/預約轉帳頁的表單操作，供各筆退款／請款腳本共用。"""

from __future__ import annotations

import re
import time
from typing import Union

from playwright.sync_api import Frame, Locator, Page

from tools.bank_statement.open_login import (
    click_nearest_control,
    current_fubon_page,
    dismiss_fubon_idle_dialog,
    visible_in_viewport,
)


Context = Union[Frame, Page]


def _contexts(page: Page) -> list[Context]:
    return [page, *page.frames]


def _all_fubon_contexts(page: Page) -> list[Context]:
    contexts: list[Context] = []
    for candidate_page in page.context.pages:
        if candidate_page.is_closed() or "ebank.taipeifubon.com.tw" not in candidate_page.url:
            continue
        contexts.extend([candidate_page, *candidate_page.frames])
    return contexts or _contexts(page)


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def click_text(page: Page, text: str, *, exact: bool = True, timeout: int = 15_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        dismiss_fubon_idle_dialog(page)
        for context in _all_fubon_contexts(page):
            try:
                item = _visible(context.get_by_text(text, exact=exact))
                if item is None:
                    continue
                try:
                    item.click(timeout=2_000)
                except Exception:
                    item.evaluate("element => element.click()")
                return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError(f"富邦頁面找不到「{text}」")


BREADCRUMB_TRANSFER_PATTERN = re.compile(r"存款/外匯/轉帳.*臺幣轉帳.*立即/預約轉帳")


def click_breadcrumb_transfer_link(page: Page, timeout: int = 15_000) -> None:
    """點畫面中間那排麵包屑「存款/外匯/轉帳 > 臺幣轉帳 > 立即/預約轉帳」最後一段的
    「立即/預約轉帳」連結，藉此重新載入空白的 STEP 1 表單。

    不能直接用 click_text 找「立即/預約轉帳」文字，因為下方分頁籤列
    （立即/預約轉帳｜轉入帳號設定｜開啟轉帳服務）也有同名文字；當畫面還
    停留在上一筆的確認資料／交易結果頁時，那個分頁只是標示目前所在分頁的
    文字，不是可以點擊重置表單的連結，點了不會有反應，導致接不到下一筆。
    這裡改成先鎖定同時包含「存款/外匯/轉帳」與「臺幣轉帳」的麵包屑那一列，
    再只在這一列裡面找「立即/預約轉帳」並點擊，就不會誤點到分頁籤列。
    """
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        dismiss_fubon_idle_dialog(page)
        for context in _all_fubon_contexts(page):
            try:
                crumb = _visible(context.get_by_text(BREADCRUMB_TRANSFER_PATTERN))
            except Exception:
                crumb = None
            if crumb is None:
                continue
            try:
                link = _visible(crumb.get_by_text("立即/預約轉帳", exact=True))
            except Exception:
                link = None
            if link is None:
                continue
            try:
                link.click(timeout=2_000)
            except Exception:
                link.evaluate("element => element.click()")
            return
        page.wait_for_timeout(200)
    raise RuntimeError("富邦頁面找不到麵包屑的『立即/預約轉帳』連結")


def _row_text_in_context(context: Context, label: str) -> str | None:
    """在單一 frame/page 裡找 label 所在列的文字；找不到就回傳 None。"""
    try:
        node = _visible(context.get_by_text(label, exact=True))
    except Exception:
        return None
    if node is None:
        return None
    for xpath in ("ancestor::tr[1]", "ancestor::div[contains(@class,'row')][1]", "parent::*"):
        row = node.locator(f"xpath={xpath}")
        if row.count():
            try:
                return row.first.inner_text(timeout=1_000)
            except Exception:
                return None
    return None


def _row_for_label(page: Page, label: str, timeout: int = 15_000) -> Locator:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for context in _contexts(page):
            try:
                node = _visible(context.get_by_text(label, exact=True))
                if node is None:
                    continue
                for xpath in ("ancestor::tr[1]", "ancestor::div[contains(@class,'row')][1]", "parent::*"):
                    row = node.locator(f"xpath={xpath}")
                    if row.count():
                        return row.first
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError(f"富邦頁面找不到欄位「{label}」")


def fill_row_inputs(page: Page, label: str, values: list[str]) -> None:
    row = _row_for_label(page, label)
    inputs = row.locator(
        'input:visible:not([type="hidden"]):not([type="radio"]):not([type="button"]):not([type="submit"])'
    )
    if inputs.count() < len(values):
        raise RuntimeError(f"「{label}」預期 {len(values)} 個輸入欄位，實際 {inputs.count()} 個")
    for index, value in enumerate(values):
        field = inputs.nth(index)
        field.click()
        field.press("Meta+A")
        field.fill(value)


def open_transfer_form(page: Page) -> Page:
    """開啟臺幣轉帳的「立即/預約轉帳」表單。

    多筆連續轉帳時，上一筆完成後停留的 STEP 2（確認資料）／STEP 3（交易
    結果）頁面同樣會顯示「轉出帳號」字樣，只看這個字判斷表單是否已開啟，
    會誤把結果頁當成可直接填寫的新表單，導致沒有點『立即/預約轉帳』就
    直接嘗試選轉出／轉入帳號，接不到下一筆。只有還沒選擇帳號的全新 STEP 1
    表單，「轉出帳號」欄位本身才會顯示「==請選擇==」下拉占位字；不能只看
    整個頁面裡有沒有這個字串，結果頁上其他不相關、還沒填的欄位也可能剛好
    殘留同樣的占位字，會誤判成表單已經是空白的。
    """
    in_transfer_section = False
    fresh_form_open = False
    for context in _contexts(page):
        try:
            body_text = context.locator("body").inner_text(timeout=1_000)
        except Exception:
            continue
        if "轉出帳號" in body_text:
            in_transfer_section = True
            source_row_text = _row_text_in_context(context, "轉出帳號")
            if source_row_text and "==請選擇==" in source_row_text:
                fresh_form_open = True
            break

    if not fresh_form_open:
        if not in_transfer_section:
            print("點擊首頁『臺幣轉帳』功能磚。")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            try:
                # 與明細下載點「我的存款」相同：排除輪播中畫面外的複本，
                # 再直接觸發真正位於視窗內的功能磚連結。
                tile = visible_in_viewport(
                    page, 'a:has-text("臺幣轉帳")', timeout_ms=8_000
                )
                tile.evaluate("el => el.click()")
            except Exception as exc:
                raise RuntimeError(f"找不到可點擊的『臺幣轉帳』：{exc}") from exc
            # 剛從首頁功能磚進入，中間那排麵包屑通常還沒出現「立即/預約轉帳」
            # 這一段，畫面上只有下方分頁籤列這一個同名文字，直接點文字即可。
            click_text(page, "立即/預約轉帳")
        else:
            # 已停留在臺幣轉帳分頁（含上一筆的確認資料／交易結果頁）時，中間
            # 那排麵包屑與下方分頁籤列會同時出現同名的「立即/預約轉帳」文字；
            # 分頁籤列此時只是標示目前所在分頁，不是可點擊重置表單的連結，
            # 點了不會有反應，必須改點麵包屑最後一段的連結才能重新載入空白表單。
            click_breadcrumb_transfer_link(page)
    _row_for_label(page, "轉出帳號", timeout=30_000)
    return current_fubon_page(page.context, page) or page


class DuplicateTransactionWarning(RuntimeError):
    """富邦顯示「系統偵測到重覆交易，請重新執行」，需要重新選一次該欄位。"""


class SavedAccountNotFound(RuntimeError):
    """帳號選擇彈出視窗裡找不到指定名稱的卡片（可能還沒被加入常用轉入帳號）。"""


def _has_duplicate_warning(page: Page) -> bool:
    for context in _contexts(page):
        try:
            if "系統偵測到重覆交易" in context.locator("body").inner_text(timeout=800):
                return True
        except Exception:
            continue
    return False


def _select_account_card(
    page: Page, row_label: str, target_text: str, *, success_label: str | None = None
) -> None:
    """點開富邦的帳號選擇彈出視窗（轉出帳號／轉入帳號皆用同一套元件：
    點擊「==請選擇==」會跳出「共找到 N 筆帳號」的卡片清單），並點選文字
    符合 target_text 的帳號卡片。"""
    label = success_label or target_text
    row = _row_for_label(page, row_label)
    if target_text in row.inner_text() and "==請選擇==" not in row.inner_text():
        print(f"{row_label}已選擇：{label}。")
        return

    # 觸發元件有時要等頁面 JS 跑完才會出現，所以要輪詢，不能只查一次。
    trigger = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and trigger is None:
        row = _row_for_label(page, row_label)
        trigger = _visible(row.get_by_text("==請選擇==", exact=False))
        if trigger is None:
            page.wait_for_timeout(200)
    if trigger is None:
        raise RuntimeError(f"{row_label}找不到可點開的下拉框")
    click_nearest_control(trigger)

    card = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and card is None:
        for context in _all_fubon_contexts(page):
            try:
                node = _visible(context.get_by_text(target_text, exact=False))
            except Exception:
                node = None
            if node is not None:
                card = node
                break
        if card is None:
            page.wait_for_timeout(200)
    if card is None:
        raise SavedAccountNotFound(f"{row_label}的帳號選擇視窗找不到「{target_text}」")
    click_nearest_control(card)

    # 選完帳號卡片後富邦會 AJAX 重建下一個欄位；尖峰時可能超過 20 秒。
    # 畫面偶爾會在選到的瞬間又蓋上「系統偵測到重覆交易」並把這欄重置，
    # 所以先短暫停頓後要再次確認選擇真的還在，而不是抓到那個瞬間就當作成功。
    #
    # 轉出帳號選定後，欄位本身的顯示文字就是「松高分行」，可以直接在 row
    # 裡找到；但轉入帳號選定後，欄位只顯示帳號（如「012(台北富邦)-...」），
    # 名字改顯示在旁邊另一列的「暱稱/轉入戶名」，不在同一個 row 裡。因此
    # 除了確認 row 不再顯示「==請選擇==」，還要在整個頁面找 target_text，
    # 不能只侷限在原本的 row。
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        row = _row_for_label(page, row_label)
        if "==請選擇==" not in row.inner_text():
            page.wait_for_timeout(800)
            if _has_duplicate_warning(page):
                raise DuplicateTransactionWarning(
                    f"富邦顯示「系統偵測到重覆交易，請重新執行」（{row_label}）"
                )
            row = _row_for_label(page, row_label)
            confirmed = target_text in row.inner_text()
            if not confirmed:
                for context in _all_fubon_contexts(page):
                    try:
                        if _visible(context.get_by_text(target_text, exact=False)) is not None:
                            confirmed = True
                            break
                    except Exception:
                        continue
            if confirmed and "==請選擇==" not in row.inner_text():
                print(f"{row_label}已選擇：{label}。")
                page.wait_for_timeout(1_500)
                return
        page.wait_for_timeout(200)
    raise RuntimeError(f"已點選{row_label}帳號卡片「{target_text}」，但畫面未更新")


def select_account_card_with_retry(
    page: Page,
    row_label: str,
    target_text: str,
    *,
    success_label: str | None = None,
    max_attempts: int = 3,
) -> None:
    # 富邦這個「重覆交易」提示本身就是指示使用者重新執行一次；
    # 依畫面指示忽略警告、重新選一次即可，不需要整頁重新載入。
    for attempt in range(1, max_attempts + 1):
        try:
            _select_account_card(page, row_label, target_text, success_label=success_label)
            return
        except DuplicateTransactionWarning:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"富邦重覆顯示「系統偵測到重覆交易」（{row_label}），"
                    f"已重試 {max_attempts} 次仍未成功，請人工確認畫面。"
                )
            print(f"富邦顯示「系統偵測到重覆交易」（{row_label}），依畫面指示重新執行（第 {attempt + 1} 次嘗試）。")
            page.wait_for_timeout(3_000)


def choose_source_account(
    page: Page, area: str, source_account: str, max_attempts: int = 3
) -> None:
    if area != "台北":
        return
    select_account_card_with_retry(page, "轉出帳號", "松高分行", max_attempts=max_attempts)


def choose_saved_destination(page: Page, name: str, max_attempts: int = 3) -> None:
    """轉入帳號點第一個框（已使用過的常用轉入帳號），依名字選卡片。"""
    select_account_card_with_retry(page, "轉入帳號", name, max_attempts=max_attempts)


def choose_manual_destination(page: Page) -> None:
    # 內部 id（如 inAcctTypeInput）在改版後可能變動，改用畫面上唯一穩定的
    # 「自行輸入」文字；選完轉出帳號後富邦會以 AJAX 延遲重建這個控制項，
    # click_text 內建輪詢會等到它出現再點。
    click_text(page, "自行輸入", exact=True, timeout=45_000)

    # 點「自行輸入」會送出 doInAccountChanged AJAX，再產生銀行與帳號欄位。
    ready_deadline = time.monotonic() + 45
    while time.monotonic() < ready_deadline:
        for context in _all_fubon_contexts(page):
            try:
                bank_select = context.locator('select[id="form1:bankList"]')
                if bank_select.count() and bank_select.is_enabled():
                    return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError("已點選「自行輸入」，但銀行選單仍為停用")


def _option_has_bank_code(option_text: str, bank_code: str) -> bool:
    return bool(re.search(rf"(^|\D){re.escape(bank_code)}(\D|$)", option_text.strip()))


def fill_manual_destination(page: Page, bank_code: str, account_number: str) -> None:
    row = _row_for_label(page, "轉入帳號")
    bank_select = row.locator('select[id="form1:bankList"]')
    if not bank_select.count():
        raise RuntimeError("自行輸入找不到銀行下拉框")
    options = bank_select.locator("option")
    target_value = ""
    for index in range(options.count()):
        option = options.nth(index)
        if _option_has_bank_code(option.inner_text(), bank_code):
            target_value = option.get_attribute("value") or ""
            break
    if not target_value:
        raise RuntimeError(f"銀行下拉框找不到代碼 {bank_code}")

    # 銀行代碼欄同樣是自製彈出視窗（點開後跳出「銀行代碼」方塊清單），
    # 不是可信賴的隱藏 select；呼叫內部函式在改版後已不可靠，改為真的
    # 點開再點代碼方塊，並用隱藏 select 的實際值（點擊後由網站自己寫入）
    # 來驗證是否真的選取成功，而不是驗證我們自己寫入的值。
    trigger = _visible(row.get_by_text("=請選擇=", exact=True))
    if trigger is None:
        raise RuntimeError("轉入帳號找不到可點開的銀行代碼下拉框")
    click_nearest_control(trigger)

    code_pattern = re.compile(rf"(^|\D){re.escape(bank_code)}(\D|$)")
    code_button = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and code_button is None:
        for context in _all_fubon_contexts(page):
            try:
                code_button = _visible(context.get_by_text(code_pattern, exact=False))
            except Exception:
                code_button = None
            if code_button is not None:
                break
        if code_button is None:
            page.wait_for_timeout(200)
    if code_button is None:
        raise RuntimeError(f"銀行代碼清單找不到代碼 {bank_code}")
    click_nearest_control(code_button)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if bank_select.input_value() == target_value:
            break
        page.wait_for_timeout(200)
    else:
        raise RuntimeError(f"已點選銀行代碼 {bank_code}，但畫面未更新")

    inputs = row.locator(
        'input:visible:not([type="hidden"]):not([type="radio"]):not([type="button"]):not([type="submit"])'
    )
    if not inputs.count():
        raise RuntimeError("自行輸入找不到轉入帳號欄")
    account_input = inputs.last
    account_input.click()
    account_input.press("Meta+A")
    account_input.fill(account_number)


def choose_immediate_date(page: Page) -> None:
    # 「立即」與當天日期同屬一個文字節點（例如「立即 2026/08/15」），
    # exact 文字比對永遠找不到單獨的「立即」；交易日期第一個單選鈕
    # 就是立即，直接點它比對文字更穩定。
    row = _row_for_label(page, "交易日期")
    radio = _visible(row.locator('input[type="radio"]'))
    if radio is None:
        raise RuntimeError("交易日期找不到可選的立即選項")
    if not radio.is_checked():
        try:
            radio.check(timeout=2_000)
        except Exception:
            radio.evaluate("el => el.click()")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if radio.is_checked():
            return
        page.wait_for_timeout(100)
    raise RuntimeError("已點選交易日期的立即選項，但未成功勾選")


def wait_confirmation(page: Page, timeout: int = 30_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for context in _contexts(page):
            try:
                text = context.locator("body").inner_text(timeout=800)
            except Exception:
                continue
            if "STEP 2" in text or "確認資料" in text or "交易密碼" in text:
                return
        page.wait_for_timeout(250)
    raise RuntimeError("已按確認，但未進入富邦確認資料頁")


def wait_user_completed_transfer(page: Page, timeout: int = 600_000) -> None:
    """等待使用者手動核對並完成本筆最終交易，才準備下一筆。"""
    print("請人工核對並完成本筆最終交易；完成後會自動準備下一筆。")
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        dismiss_fubon_idle_dialog(page)
        for context in _contexts(page):
            try:
                text = context.locator("body").inner_text(timeout=800)
            except Exception:
                continue
            if any(marker in text for marker in ("交易成功", "轉帳成功", "交易已完成")):
                return
        page.wait_for_timeout(500)
    raise RuntimeError("等待人工完成富邦交易逾時；尚未準備下一筆")
