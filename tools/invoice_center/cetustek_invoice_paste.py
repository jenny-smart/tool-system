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
    raise RuntimeError("目前頁面找不到『貼上發票資料』按鈕")


def _is_invoice_create_page(page: Any) -> bool:
    try:
        if _visible(page.locator("#lemon-ei-fill-btn")):
            return True
        breadcrumb = page.get_by_text("發票開立", exact=True)
        return _visible(breadcrumb)
    except Exception:
        return False


def _open_invoice_create(page: Any) -> None:
    if _is_invoice_create_page(page):
        try:
            _paste_button(page)
            print("[鯨躍] 已在發票開立頁", flush=True)
            return
        except Exception:
            pass

    # 明確從左側選單進入「電子發票作業 > 發票開立」；不能只因頁面上
    # 出現任意『發票開立』文字就當作已到正確頁面。
    menu = page.get_by_text("電子發票作業", exact=True)
    if _visible(menu):
        menu.first.click()
        page.wait_for_timeout(300)

    invoice_link = page.get_by_text("發票開立", exact=True)
    if not _visible(invoice_link):
        raise RuntimeError("找不到左側『發票開立』選單")
    invoice_link.first.click()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            _paste_button(page)
            print("[鯨躍] 已進入發票開立頁，找到『貼上發票資料』", flush=True)
            return
        except Exception:
            page.wait_for_timeout(200)
    raise RuntimeError("已點『發票開立』，但 10 秒內找不到『貼上發票資料』按鈕")


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
    dialogs: list[str] = []
    errors: list[str] = []

    def on_dialog(dialog: Any) -> None:
        try:
            dialogs.append(dialog.type)
            if dialog.type == "prompt":
                dialog.accept(payload_json)
            else:
                dialog.accept()
        except Exception as exc:
            errors.append(str(exc))

    _clear_dialog_handlers(page)
    page.on("dialog", on_dialog)
    try:
        button = _paste_button(page)
        button.scroll_into_view_if_needed()
        print("[鯨躍] 點擊『貼上發票資料』", flush=True)
        button.click(force=True)

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if errors:
                raise RuntimeError(errors[0])
            if "prompt" in dialogs and len(dialogs) >= 2:
                break
            page.wait_for_timeout(100)

        if "prompt" not in dialogs:
            raise RuntimeError("已點『貼上發票資料』，但未出現 Payload 輸入視窗")
        if len(dialogs) < 2:
            raise RuntimeError("Payload 已填入，但未收到第二個確認訊息")
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
