"""Invoice helpers for Taipei fixed-expense requests."""

from __future__ import annotations

import html
import re
from decimal import Decimal, ROUND_HALF_UP
from urllib.request import Request, urlopen

TRADEVAN_LINK_RE = re.compile(
    r'href=["\'](https://eci\.tradevan\.com\.tw/APSSIC/receiver/rec002\.action\?[^"\']+)["\']',
    re.IGNORECASE,
)
TRADEVAN_TOTAL_RE = re.compile(r"發票總金額\s*[:：]?\s*(?:NT\$|TWD|\$)?\s*([\d,]+)", re.IGNORECASE)
NEWEBPAY_COMPANY_RE = re.compile(r"親愛的\s*([^，,]+?)\s*[，,]\s*您好")
NEWEBPAY_AMOUNT_RE = re.compile(r"發票金額\s*[:：]\s*\$?\s*([\d,]+)\s*TWD", re.IGNORECASE)


def _round_twd(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def extract_html_body(msg) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload:
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def tradevan_invoice_link(msg) -> str:
    body = extract_html_body(msg)
    match = TRADEVAN_LINK_RE.search(body)
    if not match:
        raise ValueError("台灣連線通知信找不到發票連結")
    return html.unescape(match.group(1))


def fetch_tradevan_invoice_amount(msg) -> int:
    url = tradevan_invoice_link(msg)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    page = raw.decode(charset, errors="replace")
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text)
    match = TRADEVAN_TOTAL_RE.search(text)
    if not match:
        raise ValueError("台灣連線發票頁找不到發票總金額")
    return int(match.group(1).replace(",", ""))


def sum_tradevan_invoices(messages) -> tuple[int, str]:
    amounts = [fetch_tradevan_invoice_amount(msg) for msg in messages]
    if not amounts:
        raise ValueError("台灣連線沒有可加總的發票")
    return sum(amounts), f"{len(amounts)}張發票加總"


def parse_newebpay_email(body_text: str) -> tuple[str, int]:
    company = NEWEBPAY_COMPANY_RE.search(body_text)
    amount = NEWEBPAY_AMOUNT_RE.search(body_text)
    if not company or not amount:
        raise ValueError("藍新金流通知信找不到公司名稱或發票金額")
    return company.group(1).strip(), int(amount.group(1).replace(",", ""))


def sum_newebpay_invoices(messages, companies: tuple[str, ...]) -> tuple[dict[str, int], int]:
    totals = {company: 0 for company in companies}
    matched = 0
    for msg in messages:
        company, amount = parse_newebpay_email(_plain_body(msg))
        if company in totals:
            totals[company] += amount
            matched += 1
    if not matched:
        raise ValueError("藍新金流沒有符合指定公司的發票")
    return totals, matched


def _plain_body(msg) -> str:
    plain = ""
    html_body = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not plain:
            plain = decoded
        elif part.get_content_type() == "text/html" and not html_body:
            html_body = decoded
    if plain.strip():
        return plain
    text = html.unescape(re.sub(r"<[^>]+>", " ", html_body))
    return re.sub(r"\s+", " ", text)
