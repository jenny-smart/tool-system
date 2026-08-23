from __future__ import annotations

import time
from typing import Any

from tools.invoice_center.invoice_payload_queue import list_pending_payloads, update_payload_status


def _visible(locator: Any) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _paste_button(page: Any) -> Any:
    button = page.locator("#lemon-ei-fill-btn")
    if _visible(button):
        return button.first
    button = page.get_by_text("貼上發票資料", exact=True)
    if _visible(button):
        return button.first
    raise RuntimeError("發票開立頁找不到「貼上發票資料」按鈕；請確認 Tampermonkey 已啟用")


def _open_invoice_create(page: Any) -> None:
    try:
        _paste_button(page)
        return
    except RuntimeError:
        pass

    direct = page.get_by_text("發票開立", exact=True)
    if _visible(direct):
        direct.first.click()
    else:
        menu = page.get_by_text("電子發票作業", exact=True)
        if _visible(menu):
            menu.first.click()
        page.get_by_text("發票開立", exact=True).first.click()
    page.wait_for_timeout(800)
    _paste_button(page)


def _paste_one(page: Any, payload_json: str) -> None:
    # 使用 expect_event 同步逐一處理 dialog，避免 page.on("dialog") listener
    # 與網站/登入階段既有 listener 同時 accept，造成 already handled。
    with page.expect_event("dialog", timeout=8000) as first_dialog_info:
        _paste_button(page).click()
    first_dialog = first_dialog_info.value
    if first_dialog.type != "prompt":
        try:
            first_dialog.dismiss()
        except Exception:
            pass
        raise RuntimeError(f"點擊「貼上發票資料」後第一個視窗不是 prompt：{first_dialog.type}")

    first_dialog.accept(payload_json)

    try:
        with page.expect_event("dialog", timeout=8000) as second_dialog_info:
            # 第一個 prompt accept 後，第二個確認視窗由頁面自行觸發。
            page.wait_for_timeout(50)
        second_dialog = second_dialog_info.value
    except Exception as exc:
        raise RuntimeError("Payload 已送出，但未收到第二個確認訊息") from exc

    try:
        second_dialog.accept()
    except Exception as exc:
        raise RuntimeError(f"第二個確認訊息無法接受：{exc}") from exc


def process_pending_invoice_payloads(page: Any, area: str) -> int:
    pending = list_pending_payloads(area)
    if not pending:
        print(f"[{area}] 沒有待貼入的發票 Payload")
        return 0

    print(f"[{area}] 待貼入發票 Payload：{len(pending)} 筆")
    _open_invoice_create(page)

    # 清除登入階段可能留下的 dialog listener，Payload 階段改用 expect_event 同步處理。
    try:
        page.remove_all_listeners("dialog")
    except Exception:
        pass

    completed = 0
    for item in pending:
        row_no = int(item.get("_row") or 0)
        order_no = str(item.get("order_no") or "").strip()
        payload_json = str(item.get("payload_json") or "").strip()
        if not payload_json:
            update_payload_status(row_no, "failed", "Payload 空白")
            print(f"[{area}] {order_no or row_no}：Payload 空白")
            continue
        try:
            _paste_one(page, payload_json)
            update_payload_status(row_no, "pasted", "已貼入發票開立頁")
            completed += 1
            print(f"[{area}] {order_no}：Payload 已貼入")
        except Exception as exc:
            update_payload_status(row_no, "failed", str(exc))
            raise RuntimeError(f"{order_no} 貼入 Payload 失敗：{exc}") from exc

    print(f"[{area}] Payload 貼入完成：{completed}/{len(pending)} 筆")
    return completed
