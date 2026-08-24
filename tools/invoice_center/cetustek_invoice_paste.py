from __future__ import annotations

import json
import re
import time
from typing import Any

from tools.invoice_center.invoice_payload_queue import (
    list_pending_payloads,
    update_payload_status,
    write_invoice_result,
)


def _visible(locator: Any) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


INVOICE_FORM_SELECTORS = (
    "#orderid",
    "#buyer_name",
    "#buyer_emailaddress",
    "#detaildata",
    "#totalamount",
)

INVOICE_CREATE_URL = "https://www.ei.com.tw/InvoiceRent/invoiceadd.jsp"
INVOICE_NO_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2})[ -]?(\d{8})(?!\d)")
MANUAL_SAVE_TIMEOUT_MS = 30 * 60 * 1000


def _is_invoice_create_url(page: Any) -> bool:
    try:
        return str(page.url or "").split("?", 1)[0].split("#", 1)[0] == INVOICE_CREATE_URL
    except Exception:
        return False


def _is_invoice_create_page(page: Any) -> bool:
    """Require the exact EI URL and native form fields, never the injected helper button."""
    try:
        return _is_invoice_create_url(page) and all(
            page.locator(selector).count() > 0 for selector in INVOICE_FORM_SELECTORS
        )
    except Exception:
        return False


def _paste_button(page: Any) -> Any:
    if not _is_invoice_create_page(page):
        raise RuntimeError("目前不是鯨躍發票開立頁，禁止貼入發票資料")

    button = page.locator("#lemon-ei-fill-btn")
    if _visible(button):
        return button.first
    raise RuntimeError(
        "發票開立頁找不到 #lemon-ei-fill-btn『貼上發票資料』按鈕，"
        "請確認 Tampermonkey 腳本已啟用"
    )


def _open_invoice_create(page: Any) -> None:
    if _is_invoice_create_page(page):
        _paste_button(page)
        print("[鯨躍] 已在發票開立頁", flush=True)
        return

    print(f"[鯨躍] 前往發票開立頁：{INVOICE_CREATE_URL}", flush=True)
    page.goto(INVOICE_CREATE_URL, wait_until="domcontentloaded", timeout=15000)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _is_invoice_create_page(page):
            try:
                _paste_button(page)
                print("[鯨躍] 已進入發票開立頁，找到『貼上發票資料』", flush=True)
                return
            except Exception:
                pass
        page.wait_for_timeout(200)
    raise RuntimeError(
        f"已開啟 {INVOICE_CREATE_URL}，但 10 秒內未出現發票開立表單；"
        f"目前頁面：{str(page.url or '未知')}"
    )


def _clear_dialog_handlers(page: Any) -> None:
    try:
        page.remove_all_listeners("dialog")
    except Exception:
        pass


def _install_save_click_marker(page: Any) -> None:
    script = """
    (() => {
      if (window.__lemonSaveMarkerInstalled) return;
      window.__lemonSaveMarkerInstalled = true;
      document.addEventListener('click', (event) => {
        if (event.target && event.target.closest && event.target.closest('#btnSave')) {
          sessionStorage.setItem('lemonInvoiceSaveClicked', '1');
        }
      }, true);
    })();
    """
    page.context.add_init_script(script=script)
    page.evaluate("sessionStorage.removeItem('lemonInvoiceSaveClicked')")
    page.evaluate(script)


def _extract_invoice_no(page: Any) -> str:
    chunks: list[str] = []
    try:
        chunks.append(str(page.locator("body").inner_text() or ""))
    except Exception:
        pass
    try:
        chunks.extend(str(value or "") for value in page.locator("input").evaluate_all(
            "els => els.map(el => el.value)"
        ))
    except Exception:
        pass
    for chunk in chunks:
        match = INVOICE_NO_RE.search(chunk.upper())
        if match:
            return f"{match.group(1)}{match.group(2)}"
    return ""


def _wait_for_manual_save(page: Any, timeout_ms: int = MANUAL_SAVE_TIMEOUT_MS) -> str:
    _install_save_click_marker(page)
    print("[鯨躍] 等待人工按『下一步』並按『儲存』；程式不會代按", flush=True)
    deadline = time.monotonic() + timeout_ms / 1000
    save_clicked = False
    while time.monotonic() < deadline:
        try:
            save_clicked = save_clicked or bool(
                page.evaluate("sessionStorage.getItem('lemonInvoiceSaveClicked') === '1'")
            )
            if save_clicked:
                invoice_no = _extract_invoice_no(page)
                if invoice_no:
                    print(f"[鯨躍] 已取得發票號碼：{invoice_no}", flush=True)
                    return invoice_no
        except Exception:
            # 人工處理網站確認視窗或頁面跳轉期間，Playwright 可能暫時無法讀取 DOM。
            pass
        page.wait_for_timeout(500)
    if not save_clicked:
        raise TimeoutError("30 分鐘內未偵測到人工按『儲存』")
    raise TimeoutError("已偵測到人工按『儲存』，但 30 分鐘內找不到發票號碼")
    try:
        page.context.remove_all_listeners("dialog")
    except Exception:
        pass


def _paste_one(page: Any, payload_json: str) -> None:
    payload = json.loads(payload_json)
    expected_order_id = str(payload.get("orderid") or "").strip()
    if not expected_order_id:
        raise RuntimeError("Payload 缺少 orderid")

    _clear_dialog_handlers(page)
    button = _paste_button(page)
    button.scroll_into_view_if_needed()
    print("[鯨躍] 點擊 #lemon-ei-fill-btn『貼上發票資料』", flush=True)

    with page.expect_event("dialog", timeout=8000) as prompt_info:
        button.click(force=True)
    prompt = prompt_info.value
    if prompt.type != "prompt":
        prompt.accept()
        raise RuntimeError(f"預期 Payload 輸入視窗，實際收到 {prompt.type}")

    with page.expect_event("dialog", timeout=8000) as confirmation_info:
        prompt.accept(payload_json)
    confirmation = confirmation_info.value
    confirmation_message = str(confirmation.message or "").strip()
    confirmation.accept()

    if not confirmation_message.startswith("已填入。"):
        raise RuntimeError(f"貼入結果異常：{confirmation_message or '沒有完成訊息'}")
    if not _is_invoice_create_page(page):
        raise RuntimeError("貼入後已離開發票開立頁")

    actual_order_id = str(page.locator("#orderid").input_value() or "").strip()
    if actual_order_id != expected_order_id:
        raise RuntimeError(
            f"發票表單驗證失敗：orderid 預期 {expected_order_id}，實際 {actual_order_id or '空白'}"
        )


def process_pending_invoice_payloads(page: Any, area: str) -> int:
    pending = list_pending_payloads(area)
    if not pending:
        print(f"[{area}] 沒有待貼入的發票 Payload")
        return 0

    print(f"[{area}] 待匯入發票 Payload：{len(pending)} 筆")
    _open_invoice_create(page)
    _clear_dialog_handlers(page)

    completed = 0
    for item in pending:
        row_no = int(item.get("_row") or 0)
        source_row = int(item.get("source_row") or 0)
        order_no = str(item.get("order_no") or "").strip()
        payload_json = str(item.get("payload_json") or "").strip()
        if not payload_json:
            update_payload_status(row_no, "failed", "Payload 空白")
            continue
        try:
            _paste_one(page, payload_json)
            update_payload_status(row_no, "awaiting_save", "已貼入；等待人工按下一步及儲存")
            invoice_no = _wait_for_manual_save(page)
            write_invoice_result(area, source_row, order_no, invoice_no)
            update_payload_status(row_no, "completed", f"已回填 O/AA：{invoice_no}")
            completed += 1
            print(f"[{area}] {order_no}：已回填 O 欄 {invoice_no}、AA 欄開立時間", flush=True)
        except Exception as exc:
            update_payload_status(row_no, "failed", str(exc))
            raise RuntimeError(f"{order_no} 匯入發票資料失敗：{exc}") from exc

        # 後續『下一步/儲存/O、AA 回填』尚未接好前，每次只處理一筆。
        break

    print(f"[{area}] 本次發票資料匯入：{completed} 筆")
    return completed
