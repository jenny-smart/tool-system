from __future__ import annotations

from dataclasses import fields
from datetime import date
from typing import Any, Mapping

from .invoice import build_add_invoice_payload, build_invoice_payload
from .models import InvoiceLineItem, InvoicePayload, InvoiceResult, format_amount


def _coerce_payload(payload: InvoicePayload | Mapping[str, Any]) -> InvoicePayload:
    if isinstance(payload, InvoicePayload):
        return payload

    valid_fields = {field.name for field in fields(InvoicePayload)}
    data = {key: value for key, value in dict(payload).items() if key in valid_fields}
    if "struts.token.name" in payload:
        data["struts_token_name"] = payload["struts.token.name"]
    return InvoicePayload(**data)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _money_or_zero(value: Any) -> str:
    return format_amount(value or "0")


def _build_backend_remark(order: Any) -> str:
    parts = []
    if _clean(getattr(order, "service_date", "")):
        parts.append(f"服務日期：{_clean(getattr(order, 'service_date', ''))}")
    if _clean(getattr(order, "service_time", "")):
        parts.append(f"服務時間：{_clean(getattr(order, 'service_time', ''))}")
    if _clean(getattr(order, "paid_status", "")):
        parts.append(f"付款狀態：{_clean(getattr(order, 'paid_status', ''))}")
    return "；".join(parts)


def build_invoice_payload_from_backend_order(
    area: str,
    order: Any,
    *,
    suffix: str = "-1",
) -> InvoicePayload:
    """Build an EI invoice payload from a lemon_backend BackendOrder-like object."""
    amount = _money_or_zero(getattr(order, "amount", "0"))
    items = list(getattr(order, "items", []) or [])
    goodname = _clean(items[0]) if items else "清潔服務"
    service_remark = _build_backend_remark(order)
    orderdate = _clean(getattr(order, "service_date", "")) or date.today().isoformat()

    return build_invoice_payload(
        area=area,
        order_no=_clean(getattr(order, "order_no", "")),
        suffix=suffix,
        orderdate=orderdate,
        saleamount=amount,
        buyer_name=_clean(getattr(order, "customer_name", "")),
        buyer_address=_clean(getattr(order, "address", "")),
        buyer_emailaddress=_clean(getattr(order, "email", "")),
        buyer_phone=_clean(getattr(order, "phone", "")),
        payway=_clean(getattr(order, "payway", "")),
        mainremark=service_remark,
        items=[
            InvoiceLineItem(
                goodcode="CLEAN",
                goodname=goodname,
                unit="式",
                quantity="1",
                unitprice=amount,
                amount=amount,
                fremark=service_remark,
            )
        ],
    )


def fetch_backend_order_invoice_payload(
    area: str,
    order_no: str,
    *,
    suffix: str = "-1",
    env_name: str | None = None,
    backend_client: Any | None = None,
) -> tuple[Any, InvoicePayload]:
    """Fetch a Lemon order via lemon_backend and convert it to an invoice payload."""
    if not _clean(order_no):
        raise ValueError("請先輸入 Lemon 訂單號")

    if backend_client is None:
        from tools.lemon_backend import BackendClient

        backend_client = BackendClient(area, env_name=env_name)

    order = backend_client.get_order(_clean(order_no))
    if order is None:
        raise LookupError(f"查無 Lemon 訂單：{order_no}")

    return order, build_invoice_payload_from_backend_order(area, order, suffix=suffix)


def preview_invoice_from_order(
    area: str,
    order_no: str,
    suffix: str = "-1",
    *,
    env_name: str | None = None,
    backend_client: Any | None = None,
) -> InvoiceResult:
    _order, payload = fetch_backend_order_invoice_payload(
        area,
        order_no,
        suffix=suffix,
        env_name=env_name,
        backend_client=backend_client,
    )
    return InvoiceResult(
        success=True,
        dry_run=True,
        message="Preview only. Lemon order was loaded; no EI request was sent.",
        payload=build_add_invoice_payload(payload),
    )


def create_invoice_from_payload(
    payload: InvoicePayload | Mapping[str, Any],
    dry_run: bool = True,
    *,
    client: Any | None = None,
    captcha: str | None = None,
    captcha_field: str = "captcha",
) -> InvoiceResult:
    invoice_payload = _coerce_payload(payload)
    data = build_add_invoice_payload(invoice_payload)

    if dry_run:
        return InvoiceResult(
            success=True,
            dry_run=True,
            message="Dry-run only. EI addInvoice.action was not called.",
            payload=data,
        )

    if client is None:
        from .client import EIInvoiceClient

        client = EIInvoiceClient(invoice_payload.area)

    if not getattr(client, "logged_in", False):
        client.login(captcha=captcha, captcha_field=captcha_field)

    return client.create_invoice(invoice_payload, dry_run=False)
