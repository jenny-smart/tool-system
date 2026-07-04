from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

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


def _looks_like_paper_invoice(text: str) -> str:
    compact = str(text or "").replace(" ", "")
    if "紙本" in compact or "三聯" in compact or "統編" in compact or "公司抬頭" in compact:
        return "是"
    if "手機載具" in compact or "會員載具" in compact or "自然人憑證" in compact:
        return "否"
    return ""


def _row_links(tr: Any, base_url: str = "") -> str:
    links: list[str] = []
    for link in tr.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        label = " ".join(link.get_text(" ", strip=True).split()) or href
        links.append(f"{label}: {urljoin(base_url, href)}")
    return "\n".join(links)


def parse_invoice_list_html(
    html: str,
    *,
    order_id: str = "",
    base_url: str = "",
) -> list[dict[str, str]]:
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
                "paper_invoice": _looks_like_paper_invoice(row_text),
                "download_links": _row_links(tr, base_url),
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
                        "paper_invoice": _looks_like_paper_invoice(text),
                        "download_links": "",
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
    captcha_field: str = "capchacode",
) -> list[dict[str, Any]]:
    from .client import EIInvoiceClient

    ei_client = client or EIInvoiceClient(area)
    if not getattr(ei_client, "logged_in", False):
        ei_client.login(captcha=captcha, captcha_field=captcha_field)
    return ei_client.query_invoice_by_order_id(order_id, date1, date2)


def query_invoices_by_period(
    date1: str,
    date2: str,
    *,
    area: str = "taipei",
    order_id: str = "",
    paper_only: bool = False,
    client: "EIInvoiceClient | None" = None,
    captcha: str | None = None,
    captcha_field: str = "capchacode",
) -> list[dict[str, Any]]:
    from .client import EIInvoiceClient

    ei_client = client or EIInvoiceClient(area)
    if not getattr(ei_client, "logged_in", False):
        ei_client.login(captcha=captcha, captcha_field=captcha_field)
    rows = ei_client.query_invoices(date1, date2, order_id=order_id)
    if paper_only:
        rows = [row for row in rows if row.get("paper_invoice") == "是"]
    return rows
