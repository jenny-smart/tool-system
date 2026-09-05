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


def _order_value(order: Any, key: str, default: str = "") -> str:
    value = getattr(order, key, "")
    if value not in (None, ""):
        return _clean(value)
    extra = getattr(order, "extra", {}) or {}
    if isinstance(extra, Mapping):
        return _clean(extra.get(key, default))
    return default


def _stored_value_invoice_source(backend_client: Any, order: Any) -> Any | None:
    """Find the customer's newest paid stored-value purchase for invoice settings."""
    if _clean(getattr(order, "payway", "")) != "儲值金":
        return None
    phone = _clean(getattr(order, "phone", ""))
    if not phone:
        return None

    candidates = []
    for candidate in backend_client.search_paid_stored_value_orders_by_phone(phone):
        if _clean(getattr(candidate, "paid_status", "")) != "已付款":
            continue
        candidates.append(candidate)
    if not candidates:
        return None

    def recency_key(candidate: Any) -> tuple[str, int]:
        extra = getattr(candidate, "extra", {}) or {}
        date_value = ""
        if isinstance(extra, Mapping):
            date_value = _clean(extra.get("paid_at") or extra.get("created_at"))
        if not date_value:
            date_value = _clean(getattr(candidate, "service_date", ""))
        order_digits = "".join(ch for ch in _clean(getattr(candidate, "order_no", "")) if ch.isdigit())
        return date_value, int(order_digits or 0)

    return max(candidates, key=recency_key)


def _apply_invoice_settings(order: Any, source: Any) -> None:
    invoice_type = _order_value(source, "invoice_type")
    is_triplicate = "三聯" in invoice_type
    buyer_identifier = _order_value(source, "buyer_identifier") if is_triplicate else ""
    buyer_name = _order_value(source, "buyer_name") if is_triplicate else ""
    if is_triplicate:
        buyer_identifier = buyer_identifier or _order_value(source, "company_no")
        buyer_name = buyer_name or _order_value(source, "company_title")

    values = {
        "invoice_type": invoice_type,
        "buyer_identifier": buyer_identifier,
        "buyer_name": buyer_name,
        "carrier_type": _order_value(source, "carrier_type"),
        "carrier_no": (
            _order_value(source, "carrier_no")
            or _order_value(source, "carrier_info")
        ),
        "donate_code": _order_value(source, "donate_code"),
    }
    for key, value in values.items():
        setattr(order, key, value)
    extra = getattr(order, "extra", None)
    if isinstance(extra, dict):
        extra["invoice_settings_source_order"] = _clean(getattr(source, "order_no", ""))


def _invoice_overrides_from_order(order: Any) -> dict[str, str]:
    buyer_identifier = _order_value(order, "buyer_identifier")
    carrier_type = _order_value(order, "carrier_type")
    carrier_no = _order_value(order, "carrier_no")
    donate_code = _order_value(order, "donate_code")

    if buyer_identifier:
        return {
            "carriertype": "",
            "carrierid1": "",
            "carrierid2": "",
            "donate": "0",
            "donatevat": "",
        }
    if donate_code or "捐贈" in carrier_type:
        return {
            "carriertype": "",
            "carrierid1": "",
            "carrierid2": "",
            "donate": "1",
            "donatevat": donate_code,
        }
    if "手機" in carrier_type:
        return {
            "carriertype": "3J0002",
            "carrierid1": carrier_no,
            "carrierid2": carrier_no,
            "donate": "0",
            "donatevat": "",
        }
    if "自然人" in carrier_type:
        return {
            "carriertype": "CQ0001",
            "carrierid1": carrier_no,
            "carrierid2": carrier_no,
            "donate": "0",
            "donatevat": "",
        }
    if "紙本" in carrier_type:
        return {
            "carriertype": "",
            "carrierid1": "",
            "carrierid2": "",
            "donate": "0",
            "donatevat": "",
        }
    return {
        "carriertype": "EJ0011",
        "carrierid1": carrier_no,
        "carrierid2": carrier_no,
        "donate": "0",
        "donatevat": "",
    }


def build_invoice_payload_from_backend_order(
    area: str,
    order: Any,
    *,
    suffix: str = "-1",
    overrides: Mapping[str, Any] | None = None,
) -> InvoicePayload:
    """Build an EI invoice payload from a lemon_backend BackendOrder-like object."""
    amount = _money_or_zero(getattr(order, "amount", "0"))
    items = list(getattr(order, "items", []) or [])
    goodname = _clean(items[0]) if items else "清潔服務"
    service_remark = _build_backend_remark(order)
    orderdate = _clean(getattr(order, "service_date", "")) or date.today().isoformat()
    buyer_identifier = _order_value(order, "buyer_identifier")
    buyer_name = _order_value(order, "buyer_name") if buyer_identifier else _order_value(order, "customer_name")
    invoice_overrides = _invoice_overrides_from_order(order)
    invoice_overrides.update(dict(overrides or {}))

    return build_invoice_payload(
        area=area,
        order_no=_clean(getattr(order, "order_no", "")),
        suffix=suffix,
        orderdate=orderdate,
        saleamount=amount,
        buyer_identifier=buyer_identifier,
        buyer_name=buyer_name,
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
        **invoice_overrides,
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

    invoice_source = _stored_value_invoice_source(backend_client, order)
    if invoice_source is not None:
        _apply_invoice_settings(order, invoice_source)

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
    captcha_field: str = "capchacode",
) -> InvoiceResult:
    invoice_payload = _coerce_payload(payload)
    data = build_add_invoice_payload(invoice_payload)

    if dry_run:
        return InvoiceResult(
            success=True,
            dry_run=True,
            message="Dry-run only. EI SOAP CreateInvoiceV3 was not called.",
            payload=data,
        )

    if client is None:
        from .lemon_invoice_api import create_invoice_by_soap

        return create_invoice_by_soap(invoice_payload, dry_run=False)

    if not getattr(client, "logged_in", False):
        client.login(captcha=captcha, captcha_field=captcha_field)

    return client.create_invoice(invoice_payload, dry_run=False)
