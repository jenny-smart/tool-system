"""Local Agent runner. Persist before irreversible actions; never blindly retry."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .triplicate import complete, prepare, validate_source

CANCEL_URL = "https://www.ei.com.tw/InvoiceRent/invoicecancel.jsp"
STATE_DIR = Path(__file__).resolve().parents[2] / "outputs" / "invoice_triplicate"


def _field_row(page, label):
    return page.get_by_text(label, exact=True).locator("xpath=ancestor::tr[1]")


def cancel_original(page, plan, before_submit) -> None:
    from .cetustek_invoice_paste import _clear_dialog_handlers
    from .invoice import to_ei_roc_date
    from .models import to_decimal

    old = plan["source"]["old_invoice"]
    order = plan["source"]["order_no"]
    _clear_dialog_handlers(page)
    page.goto(CANCEL_URL)
    page.wait_for_load_state("domcontentloaded")
    if page.url.split("?")[0] != CANCEL_URL:
        raise RuntimeError("未進入已登入的發票作廢頁")
    page.get_by_text("已開立發票", exact=True).click()
    numbers = _field_row(page, "發票號碼").locator('input:not([type="hidden"])')
    dates = _field_row(page, "發票日期").locator('input:not([type="hidden"])')
    if numbers.count() != 2 or dates.count() != 2:
        raise RuntimeError("作廢查詢欄位與預期不符，未送出")
    for index in range(2):
        numbers.nth(index).fill(old)
    dates.nth(0).fill(to_ei_roc_date(plan["paid_date"]))
    dates.nth(1).fill(to_ei_roc_date(datetime.now(ZoneInfo("Asia/Taipei")).date()))
    dates.nth(1).press("Tab")
    page.get_by_text("搜尋", exact=True).click()
    result = page.locator("tr").filter(has=page.get_by_role("cell", name=old, exact=True))
    result.wait_for(state="visible", timeout=15000)
    cells = result.locator(":scope > td").all_text_contents()
    if len(cells) < 10 or cells[3].strip() != order or cells[4].strip():
        raise RuntimeError("原發票搜尋結果並非唯一且相符的二聯訂單")
    if cells[0].strip().replace("/", "-") != plan["paid_date"]:
        raise RuntimeError("原發票日期與後台付款日期不符")
    if to_decimal(cells[8]) != to_decimal(plan["total"]):
        raise RuntimeError("原發票總額與後台金額不符")
    result.locator('img[title="作廢"][src$="Abort.png"]').click()
    reason = _field_row(page, "作廢原因").locator('input:not([type="hidden"])')
    reason.fill("開立錯誤")
    if reason.input_value() != "開立錯誤":
        raise RuntimeError("作廢原因未填妥")
    messages = []

    def on_dialog(dialog):
        messages.append(dialog.message)
        if dialog.type == "alert" or re.search(r"作廢|確定|確認", dialog.message):
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", on_dialog)
    try:
        before_submit()
        page.get_by_text("確認作廢", exact=True).click()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            texts = messages + [page.locator("body").inner_text()]
            if any(re.search(r"(?:發票)?作廢(?:作業)?成功|成功作廢", text) for text in texts):
                return
            # Same invoice row must explicitly report cancelled, not merely disappear.
            if result.count() == 1:
                status = result.locator(":scope > td").nth(9)
                status_text = status.inner_text() + " ".join(status.locator("img").evaluate_all(
                    "els => els.map(e => (e.title || '') + ' ' + (e.alt || ''))"))
                if "已作廢" in status_text:
                    return
            page.wait_for_timeout(300)
        raise RuntimeError("未確認作廢成功；已停止，請核對原發票狀態，勿重複作廢")
    finally:
        page.remove_listener("dialog", on_dialog)


def process(page, ws, plan, state, save, backend, area):
    from .cetustek_invoice_paste import (
        _extract_invoice_no_for_order, _open_invoice_create, _paste_one, _wait_for_manual_save,
    )
    source = plan["source"]
    if state.get("plan") and state["plan"] != plan:
        raise RuntimeError("此原發票已有不同的處理記錄，請先核對既有進度")
    if state.get("invoice_no"):
        complete(ws, plan, state["invoice_no"])
        save({"stage": "completed"})
        print(f"已完成：{state['invoice_no']}；未重複作廢或開立", flush=True)
        return
    validate_source(ws, source)
    stage = state.get("stage", "new")
    if stage == "cancel_submitting":
        raise RuntimeError("上次作廢結果尚未確認，請人工核對；禁止自動重試或開立")
    if stage == "new":
        fresh = prepare(area, source, backend)
        if fresh != plan:
            raise RuntimeError("後台資料已變更，請回功能頁重新預覽")
        print(f"核對完成：{source['order_no']}／{source['old_invoice']}；開始作廢", flush=True)
        cancel_original(page, plan, lambda: save({"plan": plan, "stage": "cancel_submitting"}))
        save({"stage": "cancelled"})
        stage = "cancelled"
    if stage == "awaiting_save":
        number = _extract_invoice_no_for_order(page, plan["payload"]["orderid"])
        if not number or number == source["old_invoice"]:
            raise RuntimeError("已填入三聯表單，請完成儲存並停留查詢結果，再重試回填；不會重開")
    elif stage == "cancelled":
        _open_invoice_create(page)
        # Persist before filling: a crash must not cause an unnoticed second issue.
        save({"stage": "awaiting_save"})
        _paste_one(page, json.dumps(plan["payload"], ensure_ascii=False))
        print("原票已作廢；三聯已填入，請按下一步及儲存", flush=True)
        number = _wait_for_manual_save(page, plan["payload"]["orderid"])
    else:
        raise RuntimeError(f"未知處理狀態：{stage}")
    if number == source["old_invoice"]:
        raise RuntimeError("讀到原發票，禁止回填")
    save({"invoice_no": number, "stage": "issued"})
    complete(ws, plan, number)
    save({"stage": "completed"})
    print(f"完成二聯改三聯：{source['old_invoice']} → {number}；已回填 O／AA", flush=True)


def run(area: str, plan: dict, cdp_url: str) -> None:
    import fcntl
    from playwright.sync_api import sync_playwright
    from tools.lemon_backend import BackendClient
    from tools.lemon_backend.stored_value_sheet import get_worksheet
    from .allowance_create import _login
    from .cetustek_login_only import load_accounts
    from .chrome_cdp import connect_existing_chrome
    from .config import normalize_area

    key = normalize_area(area)
    identity = hashlib.sha256(f"{key}:{plan['source']['old_invoice']}".encode()).hexdigest()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{identity}.json"
    # All reissues on this host share one browser and must not overlap.
    with (STATE_DIR / "runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(path.read_text()) if path.exists() else {}

        def save(changes):
            state.update(changes)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)

        with sync_playwright() as playwright:
            _browser, context = connect_existing_chrome(playwright, cdp_url)
            page = _login(context, area, load_accounts(None))
            process(page, get_worksheet(area), plan, state, save, BackendClient(key), key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="二聯改三聯")
    parser.add_argument("--area", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args()
    run(args.area, json.loads(args.plan), args.cdp_url)
