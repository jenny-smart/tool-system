from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from .config import PURCHASE_FILTER_PARAMS, get_booking_endpoint
from .models import BackendOrder, PurchaseBlock
from .session import LemonBackendSession


ORDER_NO_RE = re.compile(r"\b(?:LC|TT)\d+\b")
INVOICE_NO_RE = re.compile(r"\b[A-Z]{2}\d{8}\b")


def normalize_phone(value: str) -> str:
    phone = re.sub(r"\D+", "", str(value or ""))
    if len(phone) == 9:
        phone = "0" + phone
    return phone


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def normalize_address(value: str) -> str:
    text = normalize_text(value).replace("臺", "台")
    return text.replace("，", ",").replace("　", "")


def extract_address_from_text(text: str) -> str:
    city = (
        r"(?:台北市|臺北市|新北市|桃園市|新竹市|新竹縣|台中市|臺中市|"
        r"彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|"
        r"屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|基隆市)"
    )
    patterns = [
        rf"({city}[^\n]*?號(?:之\d+)?(?:\d+樓)?(?:之\d+)?(?:\d+室)?)",
        rf"({city}[^\n]*?樓之\d+)",
        rf"({city}[^\n]*?\d+樓)",
        rf"({city}[^\n]*?號)",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""))
        if match:
            return match.group(1).strip()
    return ""


def get_purchase_id_from_edit_url(edit_url: str) -> str:
    match = re.search(r"/purchase/edit/(\d+)", edit_url or "")
    return match.group(1) if match else ""


def purchase_params(**overrides: Any) -> dict[str, Any]:
    params = dict(PURCHASE_FILTER_PARAMS)
    params.update({key: value for key, value in overrides.items() if value is not None})
    return params


def get_purchase_page(session: LemonBackendSession, params: dict[str, Any] | None = None):
    response = session.get("/purchase", params=params or {})
    response.raise_for_status()
    return response


def get_booking_page(session: LemonBackendSession, payway: str):
    response = session.get(get_booking_endpoint(payway))
    response.raise_for_status()
    return response


def extract_order_cards_from_purchase_html(html: str, base_url: str = "") -> list[PurchaseBlock]:
    soup = BeautifulSoup(html or "", "html.parser")
    rows = soup.select("table tbody tr") or soup.select("tr")
    blocks: list[PurchaseBlock] = []

    for row in rows:
        text = row.get_text("\n", strip=True)
        match = ORDER_NO_RE.search(text)
        if not match:
            continue
        link = row.select_one('a[href*="/purchase/edit/"]')
        edit_url = ""
        if link and link.get("href"):
            href = link["href"]
            edit_url = f"{base_url}{href}" if href.startswith("/") else href
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        blocks.append(
            PurchaseBlock(
                order_no=match.group(0),
                lines=lines,
                edit_url=edit_url,
                purchase_id=get_purchase_id_from_edit_url(edit_url),
            )
        )

    if blocks:
        return _dedupe_blocks(blocks)

    lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]
    current: PurchaseBlock | None = None
    for line in lines:
        if ORDER_NO_RE.fullmatch(line):
            if current:
                blocks.append(current)
            current = PurchaseBlock(order_no=line, lines=[line])
        elif current:
            current.lines.append(line)
    if current:
        blocks.append(current)
    return _dedupe_blocks(blocks)


def _dedupe_blocks(blocks: list[PurchaseBlock]) -> list[PurchaseBlock]:
    seen: dict[str, PurchaseBlock] = {}
    for block in blocks:
        seen[block.order_no] = block
    return list(seen.values())


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _extract_name(text: str) -> str:
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", line):
            return line
    return _first_match(r"(?:姓名|客戶|客人|會員)[：:]\s*([^\s\n]+)", text)


def _extract_money(text: str) -> str:
    raw = str(text or "").replace(",", "")
    for label in ["訂單總金額", "總金額", "合計", "總計", "金額"]:
        match = re.search(rf"{label}\s*[：:]?\s*\$?\s*(-?\d+(?:\.\d+)?)", raw)
        if match:
            value = match.group(1)
            number = float(value)
            return str(int(number)) if number.is_integer() else str(number)
    return ""


def _extract_payway(text: str) -> str:
    match = re.search(r"付款方式[：:]\s*([^\s\n]+)", text)
    if match:
        return match.group(1).strip()
    if "儲值金" in text:
        return "儲值金"
    if "信用卡" in text:
        return "信用卡"
    if "ATM" in text:
        return "ATM"
    return ""


def _extract_paid_status(text: str) -> str:
    compact = normalize_text(text)
    if "已退款" in compact:
        return "已退款"
    if "取消訂單" in compact or "已取消" in compact:
        return "取消訂單"
    if "待付款" in compact or "未付款" in compact:
        return "未付款"
    if "已付款" in compact:
        return "已付款"
    return ""


def _extract_invoice_no(text: str, order_no: str) -> str:
    cleaned = text.replace(order_no, " ")
    base_order = order_no.split("-", 1)[0]
    cleaned = cleaned.replace(base_order, " ")
    match = INVOICE_NO_RE.search(cleaned)
    return match.group(0) if match else ""


def _extract_service_date_time(text: str) -> tuple[str, str]:
    date_match = re.search(r"服務日期[\s\S]{0,80}?(\d{4}-\d{2}-\d{2})", text)
    if not date_match:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    service_date = date_match.group(1) if date_match else ""
    if not date_match:
        return "", ""
    tail = text[date_match.end():date_match.end() + 600]
    time_match = re.search(r"(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})", tail)
    if not time_match:
        time_match = re.search(r"(\d{1,2}:\d{2})\s*[-~～]\s*(\d{1,2}:\d{2})", text)
    service_time = f"{time_match.group(1)} - {time_match.group(2)}" if time_match else ""
    return service_date, service_time


def _extract_items(lines: list[str]) -> list[str]:
    labels = ["居家清潔", "辦公室清潔", "裝修細清", "大掃除", "除塵蟎", "收納"]
    return [line for line in lines if any(label in line for label in labels)]


def parse_order_block(block: PurchaseBlock) -> BackendOrder:
    text = block.raw_text
    service_date, service_time = _extract_service_date_time(text)
    phone = _first_match(r"((?:\+?886[-\s]?)?0?9[\d\-\s]{8,12})", text)
    email = _first_match(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    return BackendOrder(
        order_no=block.order_no,
        customer_name=_extract_name(text),
        phone=normalize_phone(phone),
        email=email,
        address=extract_address_from_text(text),
        amount=_extract_money(text),
        payway=_extract_payway(text),
        paid_status=_extract_paid_status(text),
        invoice_no=_extract_invoice_no(text, block.order_no),
        service_date=service_date,
        service_time=service_time,
        items=_extract_items(block.lines),
        raw_text=text,
        raw_lines=block.lines,
        edit_url=block.edit_url,
        purchase_id=block.purchase_id,
    )


def parse_purchase_list_page(html: str, base_url: str = "") -> list[BackendOrder]:
    return [parse_order_block(block) for block in extract_order_cards_from_purchase_html(html, base_url)]


def search_order(session: LemonBackendSession, order_no: str) -> list[BackendOrder]:
    target = str(order_no or "").strip()
    response = get_purchase_page(session, purchase_params(orderNo=target))
    return [order for order in parse_purchase_list_page(response.text, session.base_url) if order.order_no == target]


def get_order(session: LemonBackendSession, order_no: str) -> BackendOrder | None:
    matches = search_order(session, order_no)
    return matches[0] if matches else None


def search_orders_by_phone(session: LemonBackendSession, phone: str) -> list[BackendOrder]:
    response = get_purchase_page(session, purchase_params(phone=normalize_phone(phone)))
    return parse_purchase_list_page(response.text, session.base_url)
