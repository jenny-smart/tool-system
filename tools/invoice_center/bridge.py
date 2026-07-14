from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from .invoice import build_add_invoice_payload, build_invoice_payload
from .models import InvoiceLineItem, InvoicePayload, InvoiceResult


def _coerce_payload(payload: InvoicePayload | Mapping[str, Any]) -> InvoicePayload:
    if isinstance(payload, InvoicePayload):
        return payload

    valid_fields = {field.name for field in fields(InvoicePayload)}
    data = {key: value for key, value in dict(payload).items() if key in valid_fields}
    if "struts.token.name" in payload:
        data["struts_token_name"] = payload["struts.token.name"]
    return InvoicePayload(**data)


def preview_invoice_from_order(
    area: str,
    order_no: str,
    suffix: str = "-1",
) -> InvoiceResult:
    payload = build_invoice_payload(
        area=area,
        order_no=order_no,
        suffix=suffix,
        items=[
            InvoiceLineItem(
                goodcode="CLEAN",
                goodname="清潔服務",
                unit="式",
                quantity="1",
                unitprice="0",
                amount="0",
                fremark="preview",
            )
        ],
        saleamount="0",
    )
    return InvoiceResult(
        success=True,
        dry_run=True,
        message="Preview only. No EI request was sent.",
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
