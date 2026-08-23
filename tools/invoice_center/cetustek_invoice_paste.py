from __future__ import annotations

import time
from typing import Any

from tools.invoice_center.invoice_payload_queue import list_pending_payloads, update_payload_status


FORM_IDS = ("orderid", "buyer_name", "invoicetype07")


def _visible(locator: Any) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _is_invoice_create_page(page: Any) -> bool:
    """Only the real invoice form counts; menu text/Tampermonkey button do not."""
    try:
        return all(_visible(page.locator(f"#{element_id}")) for element_id in FORM_IDS)
    except Exception:
        return False


def _paste_button(page: Any) -> Any:
    if not _is_invoice_create_page(page):
        raise RuntimeError("尚未進入真正的發票開立表單")
    button = page.locator("#lemon-ei-fill-btn")
    if _visible(button):
        return button.first
    raise RuntimeError("已進入發票開立頁，但找不到『貼上發票資料』按鈕；請確認 Tampermonkey 已啟用")


def _wait_invoice_form(page: Any, timeout_seconds: float = 12.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_invoice_create_page(page):
            print(f"[鯨躍] 已確認真正發票開立表單：{page.url}", flush=True)
            return
        page.wait_for_timeout(200)
    raise RuntimeError(f"未進入真正的發票開立表單，目前頁面：{page.url}")


def _open_invoice_create(page: Any) -> None:
    if _is_invoice_create_page(page):
        print(f"[鯨躍] 已在真正的發票開立表單：{page.url}", flush=True)
        _paste_button(page)
        return

    print(f"[鯨躍] 目前不是發票開立表單，開始導向：{page.url}", flush=True)

    menu = page.get_by_text("電子發票作業", exact=True)
    if _visible(menu):
        try:
            menu.first.click()
            page.wait_for_timeout(300)
        except Exception:
            pass

    # 優先點真正的 link，避免命中首頁說明文字。
    invoice_link = page.get_by_role("link", name="發票開立", exact=True)
    if not _visible(invoice_link):
        candidates = page.locator("a").filter(has_text="發票開立")
        if _visible(candidates):
            invoice_link = candidates
    if not _visible(invoice_link):
        raise RuntimeError(f"找不到左側『發票開立』連結，目前頁面：{page.url}")

    before_url = page.url
    invoice_link.first.click()
    print(f"[鯨躍] 已點左側『發票開立』，原頁面：{before_url}", flush=True)
    _wait_invoice_form(page)
    _paste_button(page)
    print("[鯨躍] 發票開立表單驗證完成，找到『貼上發票資料』", flush=True)


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
    if not _is_invoice_create_page(page):
        raise RuntimeError(f"貼入前頁面驗證失敗，不是發票開立表單：{page.url}")

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
        if not _is_invoice_create_page(page):
            raise RuntimeError("Payload 處理後已離開發票開立表單，視為失敗")
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
        break

    print(f"[{area}] 本次發票資料匯入：{completed} 筆")
    return completed
