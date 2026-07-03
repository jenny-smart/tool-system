from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .config import get_secret


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

    @property
    def response_summary(self) -> str:
        return (self.response_text or "").strip()[:500]


def get_lemon_api_base_url() -> str:
    return (get_secret("LEMON_API_BASE_URL") or DEFAULT_LEMON_API_BASE_URL).rstrip("/")


def make_invoice(
    purchase_id: str,
    *,
    invoice_type: str = "一般發票",
    base_url: str | None = None,
    timeout: int = 30,
) -> LemonInvoiceApiResult:
    purchase_id_text = str(purchase_id or "").strip()
    if not purchase_id_text:
        raise ValueError("缺少 purchase_id，無法開立發票")

    option = INVOICE_TYPE_OPTIONS.get(invoice_type) or INVOICE_TYPE_OPTIONS["一般發票"]
    api_base_url = (base_url or get_lemon_api_base_url()).rstrip("/")
    url = f"{api_base_url}{option['endpoint']}"
    data: dict[str, Any] = {
        option["purchase_id_field"]: purchase_id_text,
    }

    response = requests.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    result = LemonInvoiceApiResult(
        success=200 <= response.status_code < 300,
        status_code=response.status_code,
        url=response.url,
        response_text=response.text or "",
    )
    if not result.success:
        raise RuntimeError(
            f"Lemon API 開票失敗：HTTP {result.status_code}，{result.response_summary}"
        )
    return result
