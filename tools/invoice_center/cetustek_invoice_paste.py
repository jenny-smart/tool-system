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
    dialogs: list[str] = []

    def on_dialog(dialog: Any) -> None:
        dialogs.append(dialog.type)
        if dialog.type == "prompt":
            dialog.accept(payload_json)
        else:
            dialog.accept()

    page.on("dialog", on_dialog)
    try:
        _paste_button(page).click()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and len(dialogs) < 2:
            page.wait_for_timeout(150)
        if "prompt" not in dialogs:
            raise RuntimeError("點擊「貼上發票資料」後未出現 Payload 輸入視窗")
        if len(dialogs) < 2:
            raise RuntimeError("Payload 已送出，但未收到第二個確認訊息")
    finally:
        try:
            page.remove_listener("dialog", on_dialog)
        except Exception:
            pass


def process_pending_invoice_payloads(page: Any, area: str) -> int:
    pending = list_pending_payloads(area)
    if not pending:
        print(f"[{area}] 沒有待貼入的發票 Payload")
        return 0

    print(f"[{area}] 待貼入發票 Payload：{len(pending)} 筆")
    _open_invoice_create(page)

    # 登入流程會掛 dialog logger；Payload 階段必須移除，否則 prompt/confirm
    # 會被兩個 listener 同時 accept，造成 already handled。
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
