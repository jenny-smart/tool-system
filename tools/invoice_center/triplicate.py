"""二聯改三聯：來源核對、買方覆寫與獨立完成回填。"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from .models import format_amount, round_money, to_decimal

INVOICE_RE = re.compile(r"[A-Z]{2}\d{8}\Z")


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
    if str(r[1]).strip() != "待處理發票" or str(r[2]).strip() != "異動發票":
        return None
    if not re.search(r"改\s*三聯", str(r[10])) or not str(r[6]).strip() or str(r[14]).strip():
        return None
    return {"source_row": row_no, "order_no": str(r[6]).strip(), "customer": str(r[7]).strip(),
            "note": str(r[10]).strip(), "old_invoice": str(r[23]).strip().upper(),
            "old_type": str(r[24]).strip()}


def candidates(values: list[list]) -> list[dict[str, Any]]:
    return [item for i, row in enumerate(values, 1) if (item := candidate(row, i))]


def validate_source(ws: Any, expected: dict[str, Any]) -> None:
    n = int(expected["source_row"])
    rows = ws.get(f"A{n}:AA{n}")
    current = candidate(rows[0] if rows else [], n)
    if current != expected:
        raise ValueError(f"清潔異動第 {n} 列已變更或已有新發票，請重新讀取")


def prepare(area: str, item: dict[str, Any], backend: Any) -> dict[str, Any]:
    from .bridge import build_invoice_payload_from_backend_order
    from .invoice import build_add_invoice_payload

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
    payload.buyer_identifier = requirements["buyer_identifier"]
    payload.buyer_name = requirements["buyer_name"]
    if requirements["remove_address"]:
        payload.buyer_address = ""
    payload.carriertype = payload.carrierid1 = payload.carrierid2 = payload.donatevat = ""
    payload.donate = "0"
    payload.hastax = "1"
    payload.saleamount = round_money(total / Decimal("1.05"))
    payload.taxamount = total - payload.saleamount
    payload.totalamount = total
    return {"source": item, "paid_date": paid_date, "total": format_amount(total),
            "payload": build_add_invoice_payload(payload)}


def complete(ws: Any, plan: dict[str, Any], invoice_no: str) -> None:
    from tools.local_agent_queue import now_text
    source = plan["source"]
    if not INVOICE_RE.fullmatch(invoice_no) or invoice_no == source["old_invoice"]:
        raise ValueError("新發票號碼無效或仍是原發票")
    n = source["source_row"]
    rows = ws.get(f"A{n}:AA{n}")
    row = list(rows[0] if rows else []) + [""] * 27
    if str(row[6]).strip() != source["order_no"] or str(row[23]).strip().upper() != source["old_invoice"]:
        raise ValueError("回填前來源訂單／原發票已變更")
    if row[14] and str(row[14]).strip() != invoice_no:
        raise ValueError("O 欄已有其他新發票，禁止覆蓋")
    if not row[14]:
        validate_source(ws, source)
    # X/Y retain the original invoice audit trail; no unrelated payment status change.
    updates = [{"range": f"O{n}", "values": [[invoice_no]]}]
    if not row[26]:
        updates.append({"range": f"AA{n}", "values": [[now_text()]]})
    ws.batch_update(updates, value_input_option="RAW")
