from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import requests

from .config import get_secret
from .invoice import build_add_invoice_payload
from .models import InvoicePayload


DEFAULT_LEMON_API_BASE_URL = "https://api.lemonclean.com.tw"

INVOICE_TYPE_OPTIONS = {
    "一般發票": {
        "endpoint": "/make_invoice",
        "purchase_id_field": "purchase_id",
    },
    "週週付發票": {
        "endpoint": "/make_weekly_price_invoice",
        "purchase_id_field": "purchase_id",
    },
    "年前發票": {
        "endpoint": "/make_new_year_invoice",
        "purchase_id_field": "purchaseId",
    },
}


@dataclass(frozen=True)
class LemonInvoiceApiResult:
    success: bool
    status_code: int
    url: str
    response_text: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    invoice_id: str = ""
    response_json: dict[str, Any] = field(default_factory=dict)

    @property
    def response_summary(self) -> str:
        return (self.response_text or "").strip()[:500]


def get_lemon_api_base_url() -> str:
    return (get_secret("LEMON_API_BASE_URL") or DEFAULT_LEMON_API_BASE_URL).rstrip("/")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_json_loads(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _extract_invoice_no(data: Mapping[str, Any], text: str = "") -> str:
    for key in ("invoice_no", "invoiceNo", "invoiceno", "invid", "invoice_number", "invoiceNumber"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    nested = data.get("data")
    if isinstance(nested, Mapping):
        found = _extract_invoice_no(nested)
        if found:
            return found
    # Fallback for plain-text responses like "success BK38930047".
    for token in str(text or "").replace("\n", " ").split():
        compact = token.strip(" ,.;:()[]{}\"'")
        if len(compact) >= 8 and compact[:2].isalpha() and compact[2:].isdigit():
            return compact
    return ""


def _extract_field(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    nested = data.get("data")
    if isinstance(nested, Mapping):
        return _extract_field(nested, *keys)
    return ""


def _payload_to_form_fields(payload: InvoicePayload | Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}

    if isinstance(payload, InvoicePayload):
        form = build_add_invoice_payload(payload)
        items = [
            {
                "goodcode": item.goodcode,
                "goodname": item.goodname,
                "unit": item.unit,
                "quantity": str(item.quantity),
                "unitprice": str(item.unitprice),
                "amount": str(item.amount_value()),
                "fremark": item.fremark,
            }
            for item in payload.items
        ]
        form.update(
            {
                "items": json.dumps(items, ensure_ascii=False),
                "invoice_payload": json.dumps(form, ensure_ascii=False),
            }
        )
        return form

    data = dict(payload)
    data["invoice_payload"] = json.dumps(data, ensure_ascii=False)
    return data


def make_invoice(
    purchase_id: str,
    *,
    invoice_type: str = "一般發票",
    payload: InvoicePayload | Mapping[str, Any] | None = None,
    base_url: str | None = None,
    timeout: int = 30,
) -> LemonInvoiceApiResult:
    purchase_id_text = _clean(purchase_id)
    if not purchase_id_text:
        raise ValueError("缺少 purchase_id，無法開立發票")

    option = INVOICE_TYPE_OPTIONS.get(invoice_type) or INVOICE_TYPE_OPTIONS["一般發票"]
    api_base_url = (base_url or get_lemon_api_base_url()).rstrip("/")
    url = f"{api_base_url}{option['endpoint']}"
    data: dict[str, Any] = _payload_to_form_fields(payload)
    data[option["purchase_id_field"]] = purchase_id_text
    data["purchase_id"] = purchase_id_text

    response = requests.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    response_json = _safe_json_loads(response.text or "")
    result = LemonInvoiceApiResult(
        success=200 <= response.status_code < 300,
        status_code=response.status_code,
        url=response.url,
        response_text=response.text or "",
        response_json=response_json,
        invoice_no=_extract_invoice_no(response_json, response.text),
        invoice_date=_extract_field(response_json, "invoice_date", "invoiceDate", "invdate"),
        invoice_id=_extract_field(response_json, "invoice_id", "invoiceId", "id"),
    )
    if not result.success:
        raise RuntimeError(
            f"Lemon API 開票失敗：HTTP {result.status_code}，{result.response_summary}"
        )
    return result
