"""二聯改三聯／修正發票金額：來源核對、買方覆寫與獨立完成回填。

修正發票金額（2026-11 Jenny）：K 欄「重新開立（修正）發票金額$9000」→ 原發票全額折讓（只能折讓、不作廢），
再以訂單原發票設定（二聯／三聯、載具照舊）開立修正金額的新發票。原發票金額可能有誤，所以舊金額不核對後台，
改由鯨躍原發票可折讓金額與清潔異動 S 欄（退款金額）核對；新金額取 K 欄並核對後台訂單金額。"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from .models import format_amount, round_money, to_decimal

INVOICE_RE = re.compile(r"[A-Z]{2}\d{8}\Z")
# 「重新開立修正發票金額$9000」「重新開立發票金額$18000」「修正發票金額$9000」都算
CORRECTION_RE = re.compile(r"(?:重新開立\s*(?:修正)?|修正)\s*發票金額\s*[：:]?\s*(?:NT)?\s*\$\s*([\d,]+(?:\.\d+)?)")
TRIPLICATE_KIND, CORRECTION_KIND = "triplicate", "correction"


def correction_amounts(note: str) -> list[str]:
    """K 欄所有「修正發票金額$金額」（去重、保留順序）。"""
    found = [format_amount(m) for m in CORRECTION_RE.findall(str(note or ""))]
    return list(dict.fromkeys(found))


def is_correction(row: list) -> bool:
    r = list(row) + [""] * 27
    return bool(CORRECTION_RE.search(str(r[10]))) and not re.search(r"(?:改|轉)\s*三聯", f"{r[2]} {r[10]}")


def correction_candidate(row: list, row_no: int) -> dict[str, Any] | None:
    r = list(row) + [""] * 27
    if str(r[1]).strip() != "待處理發票" or not is_correction(r) or not str(r[6]).strip() or str(r[14]).strip():
        return None
    amounts = correction_amounts(r[10])
    return {"kind": CORRECTION_KIND, "source_row": row_no, "order_no": str(r[6]).strip(),
            "customer": str(r[7]).strip(), "note": str(r[10]).strip(),
            "old_invoice": str(r[23]).strip().upper(), "old_type": str(r[24]).strip(),
            "old_total": format_amount(str(r[18]).strip()) if str(r[18]).strip() else "",
            "new_total": amounts[0] if len(amounts) == 1 else ""}


def correction_blocker(item: dict[str, Any] | None) -> str:
    """修正發票金額還不能執行的原因（空字串＝可以執行）。"""
    if item is None:
        return "K 欄沒有「修正發票金額$金額」，或 O 欄已有新發票"
    if not item["new_total"] or to_decimal(item["new_total"]) <= 0:
        return "K 欄要有唯一的「修正發票金額$金額」"
    if not INVOICE_RE.fullmatch(item["old_invoice"]):
        return "缺原發票號碼（X）"
    if not item["old_total"] or to_decimal(item["old_total"]) <= 0:
        return "缺原發票金額（S 退款金額），無法核對鯨躍原發票"
    return ""


def source_candidate(row: list, row_no: int, kind: str = TRIPLICATE_KIND) -> dict[str, Any] | None:
    return correction_candidate(row, row_no) if kind == CORRECTION_KIND else candidate(row, row_no)


def parse_requirements(note: str) -> dict[str, Any]:
    text = str(note or "").strip()
    matches = list(re.finditer(r"(?<![A-Za-z0-9])(\d{8})(?!\d)\s+([^\n；;]+)", text))
    if len(matches) != 1:
        raise ValueError("K 欄須有唯一的 8 碼統編及公司名稱（統編 公司名稱）")
    match = matches[0]
    name = re.split(r"\s*[/／]\s*|地址(?:要)?(?:刪除|移除)", match[2])[0].strip()
    if not name or not re.search(r"公司|商行|企業|工作室|事務所|協會|基金會|行$|社$", name):
        raise ValueError("無法明確辨識 K 欄公司名稱，請整理為『統編 公司名稱』獨立一行")
    return {
        "buyer_identifier": match[1], "buyer_name": name,
        "remove_address": bool(re.search(r"地址\s*(?:要|需|請)?\s*(?:刪除|移除)|(?:刪除|移除)\s*地址", text)),
    }


def candidate(row: list, row_no: int) -> dict[str, Any] | None:
    r = list(row) + [""] * 27
    detail = str(r[2]).strip()
    if str(r[1]).strip() != "待處理發票" or detail not in {"異動發票", "二聯轉三聯", "二聯改三聯"}:
        return None
    if not re.search(r"(?:改|轉)\s*三聯", f"{detail} {r[10]}") or not str(r[6]).strip() or str(r[14]).strip():
        return None
    return {"source_row": row_no, "order_no": str(r[6]).strip(), "customer": str(r[7]).strip(),
            "note": str(r[10]).strip(), "old_invoice": str(r[23]).strip().upper(),
            "old_type": str(r[24]).strip()}


def candidates(values: list[list]) -> list[dict[str, Any]]:
    return [item for i, row in enumerate(values, 1) if (item := candidate(row, i))]


def validate_source(ws: Any, expected: dict[str, Any]) -> None:
    n = int(expected["source_row"])
    rows = ws.get(f"A{n}:AA{n}")
    current = source_candidate(rows[0] if rows else [], n, expected.get("kind", TRIPLICATE_KIND))
    if current != expected:
        raise ValueError(f"清潔異動第 {n} 列已變更或已有新發票，請重新讀取")


def _single_line_note(value: str) -> str:
    """將既有 K 欄多行內容整理成以全形逗點分隔的單行。"""
    parts = [part.strip().strip("，,") for part in re.split(r"[\r\n]+", str(value or ""))]
    return "，".join(part for part in parts if part)


def prepare(area: str, item: dict[str, Any], backend: Any, *, original_action: str = "cancel") -> dict[str, Any]:
    from .bridge import build_invoice_payload_from_backend_order
    from .invoice import build_add_invoice_payload

    if original_action not in {"cancel", "allowance"}:
        raise ValueError("不支援的原發票處理方式")
    if item.get("kind") == CORRECTION_KIND:
        return prepare_correction(area, item, backend)  # 修正發票金額一律全額折讓，不作廢
    requirements = parse_requirements(item["note"])
    old = item["old_invoice"]
    if not INVOICE_RE.fullmatch(old) or "二聯" not in item["old_type"]:
        raise ValueError("X 欄原發票號碼或 Y 欄二聯式資料不完整")
    order = backend.get_order(item["order_no"])
    if order is None or order.order_no != item["order_no"]:
        raise ValueError("檸檬後台找不到相同訂單")
    # The edit page may already contain the requested company invoice settings.
    # It does not describe the invoice already issued. X/Y identify that original;
    # cancel_original additionally requires its EI buyer tax ID to be blank.
    if order.invoice_no.strip().upper() != old:
        raise ValueError(f"原發票號碼不符：後台 {order.invoice_no or '空白'}／清潔異動表 {old}")
    if order.paid_status != "已付款":
        raise ValueError("訂單尚未付款")
    paid = str((order.extra or {}).get("paid_at") or "")
    if not paid:
        match = re.search(r"付款日期\s*[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2})", order.raw_text)
        paid = match[1] if match else ""
    try:
        paid_date = datetime.strptime(paid[:10].replace("/", "-"), "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("後台缺少有效付款日期，禁止使用服務日期代替") from exc
    total = to_decimal(order.amount)
    if total <= 0:
        raise ValueError("後台訂單金額必須大於零")
    # A stable, distinct order reference lets retries distinguish the replacement.
    payload = build_invoice_payload_from_backend_order(area, order, suffix=f"-R{old}")
    # 二聯改三聯不帶原訂單／服務日期；鯨躍畫面保留預設的開立當日。
    payload.orderdate = ""
    payload.buyer_identifier = requirements["buyer_identifier"]
    payload.buyer_name = requirements["buyer_name"]
    if requirements["remove_address"]:
        payload.buyer_address = ""
    payload.carriertype = payload.carrierid1 = payload.carrierid2 = payload.donatevat = ""
    payload.donate = "0"
    payload.hastax = "2"
    payload.saleamount = round_money(total / Decimal("1.05"))
    payload.taxamount = total - payload.saleamount
    payload.totalamount = total
    return {"source": item, "paid_date": paid_date, "total": format_amount(total),
            "payload": build_add_invoice_payload(payload),
            **({"original_action": "allowance"} if original_action == "allowance" else {})}


def _original_untaxed(item: dict[str, Any], order: Any) -> Decimal:
    """鯨躍折讓頁「應稅」銷售額：二聯是含稅總額，三聯是未稅額。"""
    total = to_decimal(item["old_total"])
    kind = item.get("old_type") or getattr(order, "invoice_type", "")
    if "三聯" in str(kind):
        return round_money(total / Decimal("1.05"))
    if "二聯" in str(kind):
        return total
    raise ValueError("無法判斷原發票是二聯或三聯（Y 欄空白、後台也沒有發票類型）")


def prepare_correction(area: str, item: dict[str, Any], backend: Any, *, original_action: str = "allowance") -> dict[str, Any]:
    from .bridge import build_invoice_payload_from_backend_order
    from .invoice import build_add_invoice_payload

    if original_action != "allowance":
        raise ValueError("修正發票金額只能全額折讓原發票，不作廢")
    reason = correction_blocker(item)
    if reason:
        raise ValueError(reason)
    old = item["old_invoice"]
    order = backend.get_order(item["order_no"])
    if order is None or order.order_no != item["order_no"]:
        raise ValueError("檸檬後台找不到相同訂單")
    if order.invoice_no.strip().upper() != old:
        raise ValueError(f"原發票號碼不符：後台 {order.invoice_no or '空白'}／清潔異動表 {old}")
    total = to_decimal(item["new_total"])
    backend_total = to_decimal(order.amount)
    if backend_total != total:
        raise ValueError(f"K 欄修正金額 ${format_amount(total)} 與後台訂單金額 ${format_amount(backend_total)} 不符")
    # 原發票年度：有付款日期用付款日期，沒有就讓折讓頁搜尋近年度
    paid = str((order.extra or {}).get("paid_at") or "")
    if not paid:
        match = re.search(r"付款日期\s*[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2})", order.raw_text or "")
        paid = match[1] if match else ""
    try:
        paid_date = datetime.strptime(paid[:10].replace("/", "-"), "%Y-%m-%d").date().isoformat()
    except ValueError:
        paid_date = ""
    allowance_amount = _original_untaxed(item, order)
    # 沿用訂單原發票設定（二聯／三聯、載具、捐贈照舊），只改金額；不帶服務日期，開立當日。
    payload = build_invoice_payload_from_backend_order(area, order, suffix=f"-R{old}")
    payload.orderdate = ""
    if str(payload.buyer_identifier or "").strip():
        payload.hastax = "2"
        payload.saleamount = round_money(total / Decimal("1.05"))
        payload.taxamount = total - payload.saleamount
    else:
        payload.saleamount = total
        payload.taxamount = Decimal("0")
    payload.totalamount = total
    for line in payload.items or []:
        line.unitprice = line.amount = total
    return {"source": item, "paid_date": paid_date, "total": format_amount(total),
            "allowance_amount": format_amount(allowance_amount),
            "payload": build_add_invoice_payload(payload), "original_action": "allowance"}


def complete(ws: Any, plan: dict[str, Any], invoice_no: str) -> None:
    from tools.local_agent_queue import now_text
    source = plan["source"]
    if not INVOICE_RE.fullmatch(invoice_no) or invoice_no in {source["old_invoice"], source["order_no"], plan["payload"]["orderid"]}:
        raise ValueError("新發票號碼無效、為訂單號或仍是原發票")
    n = source["source_row"]
    rows = ws.get(f"A{n}:AA{n}")
    row = list(rows[0] if rows else []) + [""] * 27
    if str(row[6]).strip() != source["order_no"] or str(row[23]).strip().upper() != source["old_invoice"]:
        raise ValueError("回填前來源訂單／原發票已變更")
    mistaken_order = str(row[14]).strip() == source["order_no"]
    if row[14] and str(row[14]).strip() != invoice_no and not mistaken_order:
        raise ValueError("O 欄已有其他新發票，禁止覆蓋")
    if not row[14]:
        validate_source(ws, source)
    # X/Y retain the original invoice audit trail; B records invoice completion.
    updates = [{"range": f"O{n}", "values": [[invoice_no]]},
               {"range": f"B{n}", "values": [["已處理發票"]]}]
    note = _single_line_note(row[10])
    for part in (f"原發票號碼：{source['old_invoice']}", f"新發票號碼：{invoice_no}"):
        if part not in note:
            note = f"{note}，{part}" if note else part
    if note != str(row[10] or ""):
        updates.append({"range": f"K{n}", "values": [[note]]})
    if not row[26]:
        updates.append({"range": f"AA{n}", "values": [[now_text()]]})
    ws.batch_update(updates, value_input_option="RAW")


def record_allowance(ws: Any, plan: dict[str, Any], allowance_no: str) -> None:
    if not re.fullmatch(r"[A-Z]{2}\d{8,}", allowance_no):
        raise ValueError("折讓單號無效，禁止繼續開立")
    validate_source(ws, plan["source"])
    n = plan["source"]["source_row"]
    values = ws.get(f"AB{n}")
    existing = str(values[0][0]).strip() if values and values[0] else ""
    if existing and existing != allowance_no:
        raise ValueError("AB 欄已有其他折讓單號，禁止覆蓋")
    if not existing:
        ws.batch_update([{"range": f"AB{n}", "values": [[allowance_no]]}], value_input_option="RAW")
