from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from .client import EIInvoiceClient


INVOICE_NO_RE = re.compile(r"\b[A-Z]{2}\d{8}\b")


def _without_order_refs(text: str, order_id: str) -> str:
    cleaned = text
    order_id = str(order_id or "").strip()
    candidates = [order_id]
    if "-" in order_id:
        candidates.append(order_id.split("-", 1)[0])
    for candidate in candidates:
        if candidate:
            cleaned = cleaned.replace(candidate, " ")
    return cleaned


def _find_invoice_no(text: str, order_id: str = "") -> str:
    match = INVOICE_NO_RE.search(_without_order_refs(text, order_id))
    return match.group(0) if match else ""


def parse_invoice_list_html(html: str, *, order_id: str = "") -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict[str, str]] = []

    for tr in soup.find_all("tr"):
        cells = [
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in tr.find_all(["td", "th"])
        ]
        if not cells:
            continue
        row_text = " | ".join(cells)
        if order_id and order_id not in row_text:
            continue
        results.append(
            {
                "orderid": order_id if order_id in row_text else "",
                "invoice_no": _find_invoice_no(row_text, order_id),
                "row_text": row_text,
            }
        )

    if not results:
        text = " ".join(soup.get_text(" ", strip=True).split())
        if not order_id or order_id in text:
            for match in INVOICE_NO_RE.finditer(_without_order_refs(text, order_id)):
                results.append(
                    {
                        "orderid": order_id if order_id in text else "",
                        "invoice_no": match.group(0),
                        "row_text": text[:500],
                    }
                )

    return results


def query_invoice_by_order_id(
    order_id: str,
    date1: str,
    date2: str,
    *,
    area: str = "taipei",
    client: "EIInvoiceClient | None" = None,
    captcha: str | None = None,
    captcha_field: str = "captcha",
) -> list[dict[str, Any]]:
    from .client import EIInvoiceClient

    ei_client = client or EIInvoiceClient(area)
    if not getattr(ei_client, "logged_in", False):
        ei_client.login(captcha=captcha, captcha_field=captcha_field)
    return ei_client.query_invoice_by_order_id(order_id, date1, date2)
