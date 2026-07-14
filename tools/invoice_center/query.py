from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from .client import EIInvoiceClient


INVOICE_NO_RE = re.compile(r"\b[A-Z]{2}\d{8}\b")


def to_roc_date(value: str) -> str:
    text = str(value or "").strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        return text
    year, month, day = parts
    if len(year) == 4:
        year = str(int(year) - 1911)
    return f"{int(year):03d}/{int(month):02d}/{int(day):02d}"


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


def _normalize_header(value: str) -> str:
    value = str(value or "").replace(" ", "")
    mapping = {
        "發票日期": "invoice_date",
        "發票號碼": "invoice_no",
        "訂單日期": "order_date",
        "訂單編號": "orderid",
        "買方統編": "buyer_identifier",
        "買方名稱": "buyer_name",
        "銷售合計": "saleamount",
        "營業稅": "taxamount",
        "總計": "totalamount",
        "付款方式": "payway",
        "載具類別": "carrier_type",
        "發票方式": "invoice_type",
        "狀態": "status",
    }
    for label, key in mapping.items():
        if label in value:
            return key
    return ""


def _row_from_cells(
    cells: list[str],
    headers: list[str],
    *,
    order_id: str,
    base_url: str,
    tr: Any,
) -> dict[str, str]:
    row_text = " | ".join(cells)
    row: dict[str, str] = {
        "orderid": order_id if order_id in row_text else "",
        "invoice_no": _find_invoice_no(row_text, order_id),
        "paper_invoice": _looks_like_paper_invoice(row_text),
        "download_links": _row_links(tr, base_url),
        "row_text": row_text,
    }
    for idx, key in enumerate(headers):
        if key and idx < len(cells):
            row[key] = cells[idx]
    if not row.get("invoice_no"):
        row["invoice_no"] = _find_invoice_no(row_text, order_id)
    if not row.get("orderid") and order_id:
        row["orderid"] = order_id if order_id in row_text else ""
    return row


def parse_invoice_list_html(
    html: str,
    *,
    order_id: str = "",
    base_url: str = "",
) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict[str, str]] = []
    headers: list[str] = []

    for tr in soup.find_all("tr"):
        cells = [
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in tr.find_all(["td", "th"])
        ]
        if not cells:
            continue
        normalized = [_normalize_header(cell) for cell in cells]
        if "invoice_no" in normalized or "orderid" in normalized:
            headers = normalized
            continue
        row_text = " | ".join(cells)
        if "無資料" in row_text or "顯示第 0 至 0" in row_text:
            continue
        if order_id and order_id not in row_text:
            continue
        results.append(
            _row_from_cells(
                cells,
                headers,
                order_id=order_id,
                base_url=base_url,
                tr=tr,
            )
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
