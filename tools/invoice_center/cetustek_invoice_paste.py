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


DIRECT_FILL_SCRIPT = """
async (d) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (value) => String(value ?? "").trim();
  const fire = (el) => {
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
    el.dispatchEvent(new Event("blur", {bubbles: true}));
  };
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (!el) return false;
    el.value = text(value);
    fire(el);
    return true;
  };
  const forceRadio = (id) => {
    const el = document.getElementById(id);
    if (!el) return false;
    document.querySelector(`label[for="${id}"]`)?.click();
    el.checked = true;
    fire(el);
    return true;
  };
  const required = ["orderid", "buyer_name", "buyer_emailaddress", "detaildata", "totalamount"];
  const missing = required.filter((id) => !document.getElementById(id));
  if (missing.length) return {ok: false, message: `缺少鯨躍欄位：${missing.join(", ")}`};

  const clearCarrier = () => {
    ["carriertype", "carrierid1", "carrierid2", "donatevat"].forEach((id) => setValue(id, ""));
    ["barcode3J0002", "barcodeCQ0001", "barcodeEJ0011"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.checked = false;
        fire(el);
      }
    });
  };
  const setPay = (payway) => {
    const pay = document.getElementById("pay");
    if (!pay) return;
    const value = text(payway);
    pay.value = value.includes("ATM") || value === "2" ? "2"
      : value.includes("信用卡") || value === "3" ? "3"
      : value.includes("儲值金") ? "5" : "1";
    fire(pay);
  };
  const isTriplicate = Boolean(text(d.buyer_identifier));
  forceRadio("invoicetype07");
  forceRadio(isTriplicate ? "hastax2" : (text(d.hastax) === "1" ? "hastax1" : "hastax2"));
  const taxMap = {"1": "businesstax1", "2": "businesstax2", "3": "businesstax3", "4": "businesstax4"};
  forceRadio(taxMap[text(d.taxtype)] || "businesstax1");
  forceRadio(`roundnum${text(d.roundnum) || "4"}`);
  setValue("rate", d.rate || "0.05");

  clearCarrier();
  setValue("orderid", d.orderid);
  setValue("orderdate", d.orderdate);
  setValue("buyer_name", d.buyer_name);
  setValue("buyer_identifier", d.buyer_identifier);
  setValue("buyer_phone", d.buyer_phone);
  setValue("buyer_address", d.buyer_address);
  setValue("buyer_emailaddress", d.buyer_emailaddress);
  setPay(d.payway);
  setValue("mainremark", d.mainremark);

  const buyerId = text(d.buyer_identifier);
  const donate = text(d.donate);
  const donatevat = text(d.donatevat);
  const carrierType = text(d.carriertype);
  const carrier1 = text(d.carrierid1);
  const carrier2 = text(d.carrierid2 || d.carrierid1);
  if (buyerId) {
    forceRadio("donate2");
    await sleep(80);
    clearCarrier();
    await sleep(80);
    clearCarrier();
  } else if (donate === "1" || donatevat) {
    forceRadio("donate1");
    await sleep(80);
    clearCarrier();
    setValue("donatevat", donatevat);
  } else if (!carrierType && !carrier1 && !carrier2) {
    forceRadio("donate2");
    await sleep(80);
    clearCarrier();
  } else {
    clearCarrier();
    forceRadio("donate0");
    await sleep(100);
    forceRadio(carrierType === "3J0002" ? "barcode3J0002"
      : carrierType === "CQ0001" ? "barcodeCQ0001" : "barcodeEJ0011");
    setValue("carriertype", carrierType || "EJ0011");
    setValue("carrierid1", carrier1);
    setValue("carrierid2", carrier2);
  }

  setValue("buyer_emailaddress", d.buyer_emailaddress);
  setValue("detaildata", d.detaildata || "");
  setValue("saleamount", d.saleamount || "");
  setValue("taxamount", d.taxamount || "");
  setValue("totalamount", d.totalamount || "");

  const email = document.getElementById("buyer_emailaddress")?.value || "";
  if (email && !email.includes("@")) return {ok: false, message: `Email 欄位異常：${email}`};
  return {ok: true, message: "已直接填入鯨躍原生表單"};
}
"""


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


def _open_invoice_create(page: Any) -> None:
    print(f"[鯨躍] 重新驗證並前往發票開立頁：{INVOICE_CREATE_URL}", flush=True)
    page.goto(INVOICE_CREATE_URL, wait_until="domcontentloaded", timeout=15000)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _is_invoice_create_page(page):
            print("[鯨躍] 第二層授權有效，已進入發票開立頁", flush=True)
            return
        page.wait_for_timeout(200)

    try:
        body_text = str(page.locator("body").inner_text() or "")
    except Exception:
        body_text = ""
    if "未授權" in body_text or page.locator("#userid").count() > 0:
        raise PermissionError("鯨躍第二層授權已失效，禁止填入發票資料")
    raise RuntimeError(
        f"已開啟 {INVOICE_CREATE_URL}，但未出現發票開立表單；"
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


def _extract_invoice_no_for_order(page: Any, order_no: str) -> str:
    expected_order = re.sub(r"\s+", "", str(order_no or "")).upper()
    if not expected_order:
        return ""
    order_pattern = re.compile(rf"{re.escape(expected_order)}(?:-\d+)?(?!\d)")
    try:
        # 鯨躍查詢結果有些欄位放在 input value、連結或 data-*，
        # 不能只讀 innerText；同時保留逐列配對，避免誤抓其他訂單發票。
        extracted = page.evaluate(
            """() => {
              const describe = (root) => {
                const parts = [root.innerText || root.textContent || ''];
                root.querySelectorAll('input, a, [data-orderid], [data-invoice]')
                  .forEach((el) => {
                    parts.push(
                      el.value || '',
                      el.textContent || '',
                      el.getAttribute('href') || '',
                      el.getAttribute('title') || '',
                      el.getAttribute('data-orderid') || '',
                      el.getAttribute('data-invoice') || ''
                    );
                  });
                return parts.join(' ');
              };
              return {
                rows: Array.from(document.querySelectorAll('tr, [role="row"]')).map(describe),
                page: describe(document.body),
              };
            }"""
        )
        if isinstance(extracted, list):
            row_texts = extracted
            page_text = ""
        elif isinstance(extracted, dict):
            row_texts = extracted.get("rows") or []
            page_text = str(extracted.get("page") or "")
        else:
            return ""

        for row_text in row_texts:
            row_upper = str(row_text or "").upper()
            compact_row = re.sub(r"\s+", "", row_upper)
            if not order_pattern.search(compact_row):
                continue
            match = INVOICE_NO_RE.search(row_upper)
            if match:
                return f"{match.group(1)}{match.group(2)}"

        # 若訂單與發票分置於不同 DOM 列，只在整頁確實包含目標訂單，
        # 且頁面僅有一個發票號碼時復原；多個號碼則保持停止，避免錯配。
        page_upper = page_text.upper()
        compact_page = re.sub(r"\s+", "", page_upper)
        if order_pattern.search(compact_page):
            invoice_numbers = {
                f"{prefix}{digits}"
                for prefix, digits in INVOICE_NO_RE.findall(page_upper)
            }
            if len(invoice_numbers) == 1:
                return invoice_numbers.pop()
    except Exception:
        pass
    return ""


def _wait_for_manual_save(
    page: Any,
    order_no: str,
    timeout_ms: int = MANUAL_SAVE_TIMEOUT_MS,
) -> str:
    _install_save_click_marker(page)
    page.bring_to_front()
    initial_invoice_no = _extract_invoice_no_for_order(page, order_no)
    print(
        f"[鯨躍] 已切到 Agent 監控分頁：{str(page.url or '未知')}；"
        "等待人工按『下一步』及『儲存』，再於是否繼續開下一張按『否』",
        flush=True,
    )
    deadline = time.monotonic() + timeout_ms / 1000
    save_clicked = False
    while time.monotonic() < deadline:
        try:
            clicked_now = bool(
                page.evaluate("sessionStorage.getItem('lemonInvoiceSaveClicked') === '1'")
            )
            if clicked_now and not save_clicked:
                print("[鯨躍] 已偵測到人工按『儲存』", flush=True)
            save_clicked = save_clicked or clicked_now

            # 儲存成功後頁面會出現新發票號碼；即使網站導頁使點擊標記遺失，
            # 仍可依實際結果完成，避免已開立卻持續等待。
            invoice_no = _extract_invoice_no_for_order(page, order_no)
            if invoice_no and (save_clicked or invoice_no != initial_invoice_no):
                print(f"[鯨躍] 已取得發票號碼：{invoice_no}", flush=True)
                return invoice_no
        except Exception:
            # 人工處理網站確認視窗或頁面跳轉期間，Playwright 可能暫時無法讀取 DOM。
            pass
        page.wait_for_timeout(500)
    if not save_clicked:
        raise TimeoutError("30 分鐘內未偵測到儲存結果")
    raise TimeoutError("已偵測到人工按『儲存』，但 30 分鐘內找不到發票號碼")

def _paste_one(page: Any, payload_json: str) -> None:
    payload = json.loads(payload_json)
    expected_order_id = str(payload.get("orderid") or "").strip()
    if not expected_order_id:
        raise RuntimeError("Payload 缺少 orderid")
    if not _is_invoice_create_page(page):
        raise RuntimeError("目前不是已授權的鯨躍發票開立頁，禁止填入資料")

    _clear_dialog_handlers(page)
    print("[鯨躍] Playwright 直接填入發票原生欄位", flush=True)
    result = page.evaluate(DIRECT_FILL_SCRIPT, payload)
    if not isinstance(result, dict) or not bool(result.get("ok")):
        message = result.get("message") if isinstance(result, dict) else ""
        raise RuntimeError(f"填入結果異常：{message or '沒有完成訊息'}")

    if not _is_invoice_create_page(page):
        raise RuntimeError("填入後已離開發票開立頁")
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

    # 上次若已人工儲存、但 Agent 未完成回填，查詢頁仍可依訂單同列復原，
    # 必須在導向新的開立頁之前處理，避免重複開立。
    for item in pending:
        row_no = int(item.get("_row") or 0)
        source_row = int(item.get("source_row") or 0)
        order_no = str(item.get("order_no") or "").strip()
        invoice_no = _extract_invoice_no_for_order(page, order_no)
        if not invoice_no:
            continue
        write_invoice_result(area, source_row, order_no, invoice_no)
        update_payload_status(row_no, "completed", f"已從查詢頁復原並回填 O/AA：{invoice_no}")
        print(
            f"[{area}] {order_no}：查詢頁已有發票 {invoice_no}，"
            "已直接回填，未重複開立",
            flush=True,
        )
        return 1

    awaiting_orders = [
        str(item.get("order_no") or "").strip()
        for item in pending
        if str(item.get("status") or "").strip() == "awaiting_save"
    ]
    if awaiting_orders:
        raise RuntimeError(
            f"{'、'.join(awaiting_orders)} 已在等待儲存結果，"
            "但目前查詢頁找不到同訂單發票；為避免重複開立，已停止"
        )

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
            invoice_no = _wait_for_manual_save(page, order_no)
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
