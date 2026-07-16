from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
import re
from typing import Iterable, Any

from .models import (
    InvoiceLineItem,
    InvoicePayload,
    format_amount,
    round_money,
    to_decimal,
)


def build_ei_order_id(order_no: str, suffix: str = "-1") -> str:
    base = str(order_no or "").strip()
    suffix_text = str(suffix or "").strip()
    if suffix_text and not suffix_text.startswith("-"):
        suffix_text = f"-{suffix_text}"
    return f"{base}{suffix_text}"


def build_detail_rows(items: Iterable[InvoiceLineItem]) -> str:
    return "\n".join(item.detail_row() for item in items)


def build_detaildata(items: Iterable[InvoiceLineItem]) -> str:
    raw = build_detail_rows(items)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")


def to_ei_roc_date(value: str | date | None) -> str:
    text = str(value or "").strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        return text
    year, month, day = parts
    try:
        if len(year) == 4:
            year = str(int(year) - 1911)
        return f"{int(year):03d}/{int(month):02d}/{int(day):02d}"
    except ValueError:
        return text


def replace_iso_dates_with_roc(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        year, month, day = match.groups()
        return to_ei_roc_date(f"{year}/{month}/{day}")

    return ISO_DATE_RE.sub(repl, str(text or ""))


def normalize_detaildata_dates(detaildata: str) -> str:
    if not detaildata:
        return detaildata
    try:
        raw = base64.b64decode(detaildata).decode("utf-8")
    except Exception:
        return detaildata
    normalized = replace_iso_dates_with_roc(raw)
    if normalized == raw:
        return detaildata
    return base64.b64encode(normalized.encode("utf-8")).decode("ascii")


def sum_line_amounts(items: Iterable[InvoiceLineItem]) -> Decimal:
    total = Decimal("0")
    for item in items:
        total += item.amount_value()
    return total


def build_invoice_payload(
    *,
    area: str,
    order_no: str = "",
    suffix: str = "-1",
    orderid: str = "",
    orderdate: str | None = None,
    items: list[InvoiceLineItem] | None = None,
    saleamount: Any | None = None,
    buyer_identifier: str = "",
    buyer_name: str = "",
    buyer_address: str = "",
    buyer_emailaddress: str = "",
    buyer_phone: str = "",
    payway: str = "",
    mainremark: str = "",
    **overrides: Any,
) -> InvoicePayload:
    line_items = list(items or [])
    resolved_orderid = orderid or build_ei_order_id(order_no, suffix)
    resolved_orderdate = to_ei_roc_date(orderdate or date.today().isoformat())
    resolved_saleamount = (
        saleamount if saleamount not in (None, "") else sum_line_amounts(line_items)
    )

    return InvoicePayload(
        area=area,
        orderid=resolved_orderid,
        orderdate=resolved_orderdate,
        items=line_items,
        saleamount=resolved_saleamount,
        buyer_identifier=buyer_identifier,
        buyer_name=buyer_name,
        buyer_address=buyer_address,
        buyer_emailaddress=buyer_emailaddress,
        buyer_phone=buyer_phone,
        payway=payway,
        mainremark=replace_iso_dates_with_roc(mainremark),
        **overrides,
    )


def _resolve_tax_amount(payload: InvoicePayload, saleamount: Decimal) -> Decimal:
    if payload.taxamount not in (None, ""):
        return to_decimal(payload.taxamount)
    if not str(payload.buyer_identifier or "").strip():
        return Decimal("0")
    return round_money(saleamount * to_decimal(payload.rate, Decimal("0.05")))


def _resolve_total_amount(
    payload: InvoicePayload,
    saleamount: Decimal,
    taxamount: Decimal,
) -> Decimal:
    if payload.totalamount not in (None, ""):
        return to_decimal(payload.totalamount)
    return saleamount + taxamount


def build_add_invoice_payload(payload: InvoicePayload) -> dict[str, Any]:
    detaildata = normalize_detaildata_dates(payload.detaildata) or build_detaildata(
        payload.items
    )
    saleamount = to_decimal(payload.saleamount)
    if saleamount == 0 and payload.items:
        saleamount = sum_line_amounts(payload.items)
    taxamount = _resolve_tax_amount(payload, saleamount)
    totalamount = _resolve_total_amount(payload, saleamount, taxamount)

    data: dict[str, Any] = {
        "roundnum": str(payload.roundnum),
        "orderid": payload.orderid,
        "orderdate": to_ei_roc_date(payload.orderdate),
        "detaildata": detaildata,
        "buyer_identifier": payload.buyer_identifier,
        "buyer_name": payload.buyer_name,
        "buyer_address": payload.buyer_address,
        "buyer_emailaddress": payload.buyer_emailaddress,
        "buyer_phone": payload.buyer_phone,
        "payway": payload.payway,
        "mainremark": replace_iso_dates_with_roc(payload.mainremark),
        "invoicetype": payload.invoicetype,
        "taxtype": payload.taxtype,
        "zerotype": payload.zerotype,
        "zeroreason": payload.zeroreason,
        "donate": payload.donate,
        "hastax": payload.hastax,
        "hasapply": payload.hasapply,
        "rate": str(payload.rate),
        "carriertype": payload.carriertype,
        "carrierid1": payload.carrierid1,
        "carrierid2": payload.carrierid2,
        "donatevat": payload.donatevat,
        "saleamount": format_amount(saleamount),
        "taxamount": format_amount(taxamount),
        "totalamount": format_amount(totalamount),
        "cd": payload.cd,
        "invid": payload.invid,
        "invdate": payload.invdate,
        "struts.token.name": payload.struts_token_name,
        "token": payload.token,
        "ctoken": payload.ctoken,
    }
    data.update(payload.extra)
    return data
