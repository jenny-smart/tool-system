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

from .triplicate import complete, prepare, record_allowance, validate_source

CANCEL_URL = "https://www.ei.com.tw/InvoiceRent/invoicecancel.jsp"
STATE_DIR = Path(__file__).resolve().parents[2] / "outputs" / "invoice_triplicate"


def _field_row(page, label):
    return page.get_by_text(label, exact=True).locator("xpath=ancestor::tr[1]")



def _set_cancel_date(field, value: str) -> None:
    # EI date-picker inputs are readonly; the submitted value still lives on the input.
    field.evaluate("""(el, value) => {
        el.value = value;
        for (const name of ['input', 'change', 'blur']) {
            el.dispatchEvent(new Event(name, {bubbles: true}));
        }
    }""", value)
    if field.input_value().strip() != value:
        raise RuntimeError(f"發票日期未成功設定為 {value}，禁止搜尋或作廢")


def _action_button(page, label: str):
    """Match visible native controls, ignoring decorative icons and spacing."""
    controls = page.locator("a,button,input[type='button'],input[type='submit'],[role='button']")
    matches = []
    for index in range(controls.count()):
        control = controls.nth(index)
        if not control.is_visible():
            continue
        text = control.evaluate("""el => {
            if (el.tagName === 'INPUT') return el.value;
            const copy = el.cloneNode(true);
            copy.querySelectorAll('i,svg,[aria-hidden="true"]').forEach(n => n.remove());
            return copy.textContent || '';
        }""")
        if re.sub(r"[\W_]+", "", text or "") == re.sub(r"[\W_]+", "", label):
            matches.append(control)
    if len(matches) != 1:
        raise RuntimeError(f"找不到唯一可見的「{label}」操作按鈕（{len(matches)} 個），未送出")
    return matches[0]


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
    expected_dates = [to_ei_roc_date(plan["paid_date"]),
                      to_ei_roc_date(datetime.now(ZoneInfo("Asia/Taipei")).date())]
    for index, value in enumerate(expected_dates):
        _set_cancel_date(dates.nth(index), value)
    if [dates.nth(i).input_value().strip() for i in range(2)] != expected_dates:
        raise RuntimeError("發票日期被日期元件重設，禁止搜尋或作廢")
    result = page.locator("#result #data > tbody > tr").filter(has=page.get_by_role("cell", name=old, exact=True))
    # EI can retain a valid result after search/navigation. Use that row directly.
    if result.count() != 1 or not result.is_visible():
        _action_button(page, "搜尋").click()
    result.wait_for(state="visible", timeout=15000)
    cells = result.locator(":scope > td").all_text_contents()
    if len(cells) < 10 or cells[3].strip() != order or cells[4].strip():
        raise RuntimeError("原發票搜尋結果並非唯一且相符的二聯訂單")
    if cells[0].strip().replace("/", "-") != plan["paid_date"]:
        raise RuntimeError("原發票日期與後台付款日期不符")
    if to_decimal(cells[8]) != to_decimal(plan["total"]):
        raise RuntimeError("原發票總額與後台金額不符")
    result.locator("a[onclick^='invoicecance(']").filter(
        has=page.locator('img[title="作廢"][src$="Abort.png"]')
    ).click()
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
        confirm = _action_button(page, "確認作廢")
        before_submit()
        confirm.click()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            texts = messages + [page.locator("body").inner_text()]
            if any(re.search(r"(?:發票)?作廢(?:作業)?成功|成功作廢", text) for text in texts):
                return
            # Same invoice row must explicitly report cancelled, not merely disappear.
            if result.count() == 1:
                status = result.locator(":scope > td").nth(9)
                status_text = status.inner_text() + " ".join(status.locator("img,[data-title]").evaluate_all(
                    "els => els.map(e => (e.title || '') + ' ' + (e.alt || '') + ' ' + (e.getAttribute('data-title') || ''))"))
                if "已作廢" in status_text:
                    return
            page.wait_for_timeout(300)
        raise RuntimeError("未確認作廢成功；已停止，請核對原發票狀態，勿重複作廢")
    finally:
        page.remove_listener("dialog", on_dialog)


def allowance_original(page, plan, before_submit):
    from .allowance_create import _create_one, _open_allowance
    from .cetustek_invoice_paste import _clear_dialog_handlers
    _clear_dialog_handlers(page)
    _open_allowance(page)
    return _create_one(page, plan["source"]["old_invoice"], plan["total"],
                       invoice_year=int(plan["paid_date"][:4]), require_full=True,
                       before_save=before_submit)


def process(page, ws, plan, state, save, backend, area, *, resume_allowance_no="",
            retry_unissued=False):
    from .cetustek_invoice_paste import (
        _extract_invoice_no_for_order, _open_invoice_create, _paste_one, _wait_for_manual_save,
    )
    source = plan["source"]
    action = plan.get("original_action", "cancel")
    stage = state.get("stage", "new")
    if action not in {"cancel", "allowance"}:
        raise ValueError("不支援的原發票處理方式")
    resume_allowance_no = str(resume_allowance_no or "").strip().upper()
    detected_allowance_no = ""
    if action == "allowance":
        values = ws.get(f"AB{source['source_row']}")
        detected_allowance_no = str(values[0][0]).strip().upper() if values and values[0] else ""
        if detected_allowance_no and not re.fullmatch(r"[A-Z]{2}\d{8,}", detected_allowance_no):
            raise ValueError("AB 欄折讓單號格式不符，請先核對")
        if resume_allowance_no and detected_allowance_no and resume_allowance_no != detected_allowance_no:
            raise ValueError("輸入的折讓單號與 AB 欄不符")
    effective_allowance_no = resume_allowance_no or detected_allowance_no
    if state.get("allowance_no") and effective_allowance_no and state["allowance_no"] != effective_allowance_no:
        raise ValueError("與已記錄的折讓單號不符")
    saved_plan = state.get("plan") or {}
    same_work = (
        saved_plan.get("source") == source
        and saved_plan.get("original_action", "cancel") == action
    )
    confirmed_retry = retry_unissued or (
        bool(effective_allowance_no) and stage == "awaiting_save" and same_work
    )
    if saved_plan and saved_plan != plan and not (
        confirmed_retry and stage == "awaiting_save" and same_work
    ):
        raise RuntimeError("此原發票已有不同的處理記錄，請先核對既有進度")
    if state.get("invoice_no"):
        complete(ws, plan, state["invoice_no"])
        save({"stage": "completed"})
        print(f"已完成：{state['invoice_no']}；未重複作廢或開立", flush=True)
        return
    validate_source(ws, source)
    if confirmed_retry:
        if stage != "awaiting_save" or not same_work:
            raise RuntimeError("只有「已填入但尚未儲存」的同一筆發票可重新填入")
        previous_order_id = str((saved_plan.get("payload") or {}).get("orderid") or "")
        number = _extract_invoice_no_for_order(page, previous_order_id)
        if number and number != source["old_invoice"]:
            save({"plan": plan, "invoice_no": number, "stage": "issued"})
            complete(ws, plan, number)
            save({"stage": "completed"})
            print(f"已找到新發票 {number}，只補回填，未重複開立", flush=True)
            return
        resume_stage = "allowed" if action == "allowance" and effective_allowance_no else "cancelled"
        changes = {"plan": plan, "stage": resume_stage}
        if effective_allowance_no:
            changes["allowance_no"] = effective_allowance_no
        save(changes)
        stage = resume_stage
        print("已確認查無新發票；保留原票處理結果，重新填入三聯發票", flush=True)
    if effective_allowance_no and not confirmed_retry:
        if action != "allowance" or stage not in {"new", "allowance_submitting", "allowed"}:
            raise RuntimeError("目前進度不能改為接續折讓，請保留既有新發票處理")
        if not re.fullmatch(r"[A-Z]{2}\d{8,}", effective_allowance_no):
            raise ValueError("已開立折讓單號格式不符")
        if stage != "allowed" or state.get("allowance_no") != effective_allowance_no:
            save({"plan": plan, "stage": "allowed", "allowance_no": effective_allowance_no})
        stage = "allowed"
    if stage in {"cancel_submitting", "allowance_submitting"}:
        raise RuntimeError("上次原票處理結果尚未確認，請人工核對；禁止自動重試或開立")
    if stage == "new":
        fresh = prepare(area, source, backend, original_action=action)
        if fresh != plan:
            raise RuntimeError("後台資料已變更，請回功能頁重新預覽")
        print(f"核對完成：{source['order_no']}／{source['old_invoice']}；原票處理：{action}", flush=True)
        if action == "allowance":
            number = allowance_original(page, plan, lambda: save({"plan": plan, "stage": "allowance_submitting"}))
            save({"stage": "allowed", "allowance_no": number})
            stage = "allowed"
        else:
            cancel_original(page, plan, lambda: save({"plan": plan, "stage": "cancel_submitting"}))
            save({"stage": "cancelled"})
            stage = "cancelled"
    if stage == "allowed":
        record_allowance(ws, plan, state["allowance_no"])
        print(f"全額折讓完成：{state['allowance_no']}；已回填 AB", flush=True)
    if stage == "awaiting_save":
        number = _extract_invoice_no_for_order(page, plan["payload"]["orderid"])
        if not number or number == source["old_invoice"]:
            raise RuntimeError("已填入三聯表單，請完成儲存並停留查詢結果，再重試回填；不會重開")
    elif stage in {"cancelled", "allowed"}:
        _open_invoice_create(page)
        # Persist before filling: a crash must not cause an unnoticed second issue.
        save({"stage": "awaiting_save"})
        _paste_one(page, json.dumps(plan["payload"], ensure_ascii=False))
        print("原票處理完成；三聯已填入，請按下一步及儲存", flush=True)
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

    plan = dict(plan)
    resume_allowance_no = str(plan.pop("resume_allowance_no", "") or "").strip().upper()
    retry_unissued = bool(plan.pop("retry_unissued", False))
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
            process(page, get_worksheet(area), plan, state, save, BackendClient(key), key,
                    resume_allowance_no=resume_allowance_no, retry_unissued=retry_unissued)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="二聯改三聯")
    parser.add_argument("--area", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args()
    run(args.area, json.loads(args.plan), args.cdp_url)
