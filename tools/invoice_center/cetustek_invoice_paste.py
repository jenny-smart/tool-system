from __future__ import annotations

import json
import time
from typing import Any

from tools.invoice_center.invoice_payload_queue import list_pending_payloads, update_payload_status


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


def _is_invoice_create_page(page: Any) -> bool:
    """Only trust native EI invoice form fields, never the injected helper button."""
    try:
        return all(page.locator(selector).count() > 0 for selector in INVOICE_FORM_SELECTORS)
    except Exception:
        return False


def _paste_button(page: Any) -> Any:
    if not _is_invoice_create_page(page):
        raise RuntimeError("目前不是鯨躍發票開立頁，禁止貼入發票資料")

    button = page.locator("#lemon-ei-fill-btn")
    if _visible(button):
        return button.first
    button = page.get_by_text("貼上發票資料", exact=True)
    if _visible(button):
        return button.first
    raise RuntimeError("發票開立頁找不到『貼上發票資料』按鈕，請確認 Tampermonkey 腳本已啟用")


def _open_invoice_create(page: Any) -> None:
    if _is_invoice_create_page(page):
        _paste_button(page)
        print("[鯨躍] 已在發票開立頁", flush=True)
        return

    # 從左側選單進入「電子發票作業 > 發票開立」。
    # Tampermonkey 按鈕會出現在所有 EI 頁面，不能拿它判斷目前頁面。
    menu = page.get_by_text("電子發票作業", exact=True)
    if _visible(menu):
        menu.first.click()
        page.wait_for_timeout(300)

    invoice_link = page.get_by_role("link", name="發票開立", exact=True)
    if not _visible(invoice_link):
        invoice_link = page.get_by_text("發票開立", exact=True)
    if not _visible(invoice_link):
        raise RuntimeError("找不到左側『發票開立』選單")
    invoice_link.first.click()

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
    raise RuntimeError("已點『發票開立』，但 10 秒內未進入發票開立表單")


def _clear_dialog_handlers(page: Any) -> None:
    try:
        page.remove_all_listeners("dialog")
    except Exception:
        pass
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
    print("[鯨躍] 點擊『貼上發票資料』", flush=True)

    with page.expect_dialog(timeout=8000) as prompt_info:
        button.click(force=True)
    prompt = prompt_info.value
    if prompt.type != "prompt":
        prompt.accept()
        raise RuntimeError(f"預期 Payload 輸入視窗，實際收到 {prompt.type}")

    with page.expect_dialog(timeout=8000) as confirmation_info:
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
        order_no = str(item.get("order_no") or "").strip()
        payload_json = str(item.get("payload_json") or "").strip()
        if not payload_json:
            update_payload_status(row_no, "failed", "Payload 空白")
            continue
        try:
            _paste_one(page, payload_json)
            update_payload_status(row_no, "pasted", "發票資料已匯入；尚未按下一步/儲存")
            completed += 1
            print(f"[{area}] {order_no}：發票資料匯入完成，停在下一步前", flush=True)
        except Exception as exc:
            update_payload_status(row_no, "failed", str(exc))
            raise RuntimeError(f"{order_no} 匯入發票資料失敗：{exc}") from exc

        # 後續『下一步/儲存/O、AA 回填』尚未接好前，每次只處理一筆。
        break

    print(f"[{area}] 本次發票資料匯入：{completed} 筆")
    return completed
