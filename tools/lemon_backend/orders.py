from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from .config import PURCHASE_FILTER_PARAMS, get_booking_endpoint
from .models import BackendOrder, PurchaseBlock
from .session import LemonBackendSession


ORDER_NO_RE = re.compile(r"\b(?:LC|TT)\d+\b")
INVOICE_NO_RE = re.compile(r"\b[A-Z]{2}\d{8}\b")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
MOBILE_BARCODE_RE = re.compile(r"(/[0-9A-Z.+-]{7})", re.IGNORECASE)
TAX_ID_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


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


def _extract_purchase_list_data(html: str) -> list[dict[str, Any]]:
    """Extract the Vue purchaseList JSON embedded in the Lemon purchase page."""
    source = str(html or "")
    marker = "purchaseList:"
    start = source.find(marker)
    if start < 0:
        return []

    brace = source.find("{", start)
    if brace < 0:
        return []

    depth = 0
    in_string = False
    escaped = False
    end = -1
    for index in range(brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    if end < 0:
        return []

    try:
        payload = json.loads(source[brace:end])
    except json.JSONDecodeError:
        return []
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def _text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _money_value(value: Any) -> str:
    text = _text_value(value).replace(",", "")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else str(number)


def _payway_from_purchase_data(item: dict[str, Any]) -> str:
    code = _text_value(item.get("payway"))
    mapping = {
        "1": "信用卡",
        "2": "ATM",
        "3": "現金",
        "4": "儲值金",
    }
    return mapping.get(code, code)


def _paid_status_from_purchase_data(item: dict[str, Any]) -> str:
    if item.get("cancel_at") or item.get("cancel_log"):
        return "取消訂單"
    if item.get("paid_at") or _text_value(item.get("purchase_status")) == "1":
        return "已付款"
    if _text_value(item.get("purchase_status")) == "0":
        return "待付款"
    return ""


def _order_status_from_purchase_data(item: dict[str, Any]) -> str:
    if item.get("cancel_at") or item.get("cancel_log"):
        return "取消訂單"
    if _text_value(item.get("progress")) == "1":
        return "已處理"
    return ""


def _invoice_fields_from_purchase_data(item: dict[str, Any]) -> dict[str, str]:
    invoice_type = _text_value(item.get("invoice_type"))
    company_no = _text_value(item.get("company_no") or item.get("tax_no"))
    company_title = _text_value(item.get("company_title"))
    donate_code = _text_value(item.get("donate_code"))
    carrier_type_id = _text_value(item.get("carrier_type_id"))
    carrier_info = _text_value(item.get("carrier_info"))
    email = _text_value(item.get("email"))

    if invoice_type == "3" or company_no:
        return {
            "invoice_type": "三聯式",
            "buyer_identifier": company_no,
            "buyer_name": company_title,
            "carrier_type": "紙本",
            "carrier_no": "",
            "donate_code": "",
        }
    if invoice_type == "1" or donate_code:
        return {
            "invoice_type": "二聯式",
            "buyer_identifier": "",
            "buyer_name": "",
            "carrier_type": "捐贈",
            "carrier_no": "",
            "donate_code": donate_code,
        }

    carrier_labels = {
        "1": "會員載具",
        "2": "手機載具",
        "3": "自然人憑證",
        "4": "紙本",
    }
    carrier_type = carrier_labels.get(carrier_type_id, "會員載具")
    carrier_no = carrier_info
    if carrier_type == "會員載具":
        carrier_no = carrier_info or email
    elif carrier_type == "紙本":
        carrier_no = ""

    return {
        "invoice_type": "二聯式",
        "buyer_identifier": "",
        "buyer_name": "",
        "carrier_type": carrier_type,
        "carrier_no": carrier_no,
        "donate_code": "",
    }


def _safe_raw_text_from_purchase_data(item: dict[str, Any], invoice_fields: dict[str, str]) -> str:
    parts = [
        _text_value(item.get("order_no")),
        _text_value(item.get("name")),
        _text_value(item.get("phone")),
        _text_value(item.get("email")),
        _text_value(item.get("address")),
        f"付款方式：{_payway_from_purchase_data(item)}",
        f"發票：{invoice_fields.get('invoice_type', '')}",
        f"載具：{invoice_fields.get('carrier_type', '')} {invoice_fields.get('carrier_no', '')}".strip(),
        f"公司：{invoice_fields.get('buyer_name', '')} {invoice_fields.get('buyer_identifier', '')}".strip(),
    ]
    return "\n".join(part for part in parts if part and not part.endswith("："))


def _order_from_purchase_data(item: dict[str, Any], base_url: str = "") -> BackendOrder:
    invoice_fields = _invoice_fields_from_purchase_data(item)
    purchase_id = _text_value(item.get("purchase_id"))
    order_no = _text_value(item.get("order_no"))
    edit_url = ""
    if purchase_id:
        root = base_url.rstrip("/")
        edit_url = f"{root}/purchase/edit/{purchase_id}?orderNo={order_no}" if root else f"/purchase/edit/{purchase_id}?orderNo={order_no}"
    service_date = _text_value(item.get("date_clean"))
    period_s = _text_value(item.get("period_s"))
    period_e = _text_value(item.get("period_e"))
    service_time = f"{period_s} - {period_e}" if period_s and period_e else ""
    item_name = _text_value(item.get("clean_type_name")) or "居家清潔"

    return BackendOrder(
        order_no=order_no,
        customer_name=_text_value(item.get("name")),
        phone=normalize_phone(_text_value(item.get("phone"))),
        email=_text_value(item.get("email")),
        address=_text_value(item.get("address")),
        amount=_money_value(item.get("total")),
        payway=_payway_from_purchase_data(item),
        order_status=_order_status_from_purchase_data(item),
        paid_status=_paid_status_from_purchase_data(item),
        invoice_no=_text_value(item.get("invoice_no")),
        invoice_type=invoice_fields["invoice_type"],
        buyer_identifier=invoice_fields["buyer_identifier"],
        buyer_name=invoice_fields["buyer_name"],
        carrier_type=invoice_fields["carrier_type"],
        carrier_no=invoice_fields["carrier_no"],
        donate_code=invoice_fields["donate_code"],
        service_date=service_date,
        service_time=service_time,
        items=[item_name],
        raw_text=_safe_raw_text_from_purchase_data(item, invoice_fields),
        raw_lines=[],
        edit_url=edit_url,
        purchase_id=purchase_id,
        source="purchase_json",
        extra={
            "purchase_data_loaded": True,
            "invoice_type_code": _text_value(item.get("invoice_type")),
            "carrier_type_id": _text_value(item.get("carrier_type_id")),
            "carrier_info": _text_value(item.get("carrier_info")),
            "company_title": _text_value(item.get("company_title")),
            "company_no": _text_value(item.get("company_no")),
            "payway_code": _text_value(item.get("payway")),
        },
    )


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _clean_invoice_value(value: str) -> str:
    text = re.split(r"\s{2,}|\||　", str(value or "").strip(), maxsplit=1)[0]
    return text.strip("：:;；,， ")


def _label_value(text: str, labels: list[str], max_length: int = 80) -> str:
    joined = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?:{joined})[ \t　]*[：:]?[ \t　]*([^\n]{{1,{max_length}}})"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _clean_invoice_value(match.group(1)) if match else ""


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
    labeled = _label_value(text, ["付款狀態"], 20)
    if labeled:
        for status in ["已付款", "待付款", "未付款", "已退款", "取消訂單", "已取消"]:
            if status in labeled:
                return "取消訂單" if status == "已取消" else status
    compact = normalize_text(text)
    if "已退款" in compact:
        return "已退款"
    if "待付款" in compact or "未付款" in compact:
        return "未付款"
    if "已付款" in compact:
        return "已付款"
    return ""


def _extract_order_status(text: str) -> str:
    labeled = _label_value(text, ["服務狀態", "訂單狀態"], 30)
    if labeled:
        for status in ["已處理", "取消訂單", "已取消", "未處理", "處理中"]:
            if status in labeled:
                return "取消訂單" if status == "已取消" else status
    compact = normalize_text(text)
    if "已處理" in compact:
        return "已處理"
    return ""


def _extract_invoice_no(text: str, order_no: str) -> str:
    cleaned = text.replace(order_no, " ")
    base_order = order_no.split("-", 1)[0]
    cleaned = cleaned.replace(base_order, " ")
    match = INVOICE_NO_RE.search(cleaned)
    return match.group(0) if match else ""


def _extract_tax_id(text: str) -> str:
    labeled = _label_value(text, ["統一編號", "統編", "公司統編", "買受人統編", "tax_id", "buyer_identifier"], 30)
    match = TAX_ID_RE.search(labeled) if labeled else None
    if match:
        return match.group(1)

    for line in text.splitlines():
        if any(label in line for label in ["統一編號", "統編", "公司統編", "買受人統編"]):
            match = TAX_ID_RE.search(line)
            if match:
                return match.group(1)
    return ""


def _extract_company_title(text: str) -> str:
    value = _label_value(text, ["公司抬頭", "發票抬頭", "買受人", "公司名稱", "buyer_name"], 120)
    if not value:
        match = re.search(r"三聯式[：:\s]*([^\n\d]{2,80}?)(?=\s*\d{8})", text)
        if match:
            value = match.group(1).strip()
    if not value:
        return ""
    value = re.sub(r"(統一編號|統編|公司統編|買受人統編).*", "", value).strip()
    value = re.sub(r"\d{8}.*$", "", value).strip("：:;；,， ")
    return value


def _extract_mobile_carrier(text: str) -> str:
    labeled = _label_value(text, ["手機載具", "手機條碼", "手機載具條碼"], 40)
    match = MOBILE_BARCODE_RE.search(labeled) if labeled else None
    if match:
        return match.group(1).upper()

    for line in text.splitlines():
        if "手機" in line and ("載具" in line or "條碼" in line):
            match = MOBILE_BARCODE_RE.search(line)
            if match:
                return match.group(1).upper()
    return ""


def _extract_member_carrier(text: str, email: str) -> str:
    value = _label_value(text, ["會員載具", "Email載具", "電子信箱載具", "載具信箱"], 80)
    if value:
        email_match = EMAIL_RE.search(value)
        if email_match:
            return email_match.group(1)
    return email


def _extract_donate_code(text: str) -> str:
    value = _label_value(text, ["愛心碼", "捐贈碼", "donatevat"], 30)
    match = re.search(r"\d{3,7}", value)
    return match.group(0) if match else ""


def _extract_invoice_fields(text: str, email: str) -> dict[str, str]:
    tax_id = _extract_tax_id(text)
    company_title = _extract_company_title(text)
    mobile_carrier = _extract_mobile_carrier(text)
    donate_code = _extract_donate_code(text)
    member_carrier = _extract_member_carrier(text, email)

    if tax_id:
        return {
            "invoice_type": "三聯式",
            "buyer_identifier": tax_id,
            "buyer_name": company_title,
            "carrier_type": "紙本",
            "carrier_no": "",
            "donate_code": "",
        }
    if donate_code:
        return {
            "invoice_type": "二聯式",
            "buyer_identifier": "",
            "buyer_name": "",
            "carrier_type": "捐贈",
            "carrier_no": "",
            "donate_code": donate_code,
        }
    if mobile_carrier:
        return {
            "invoice_type": "二聯式",
            "buyer_identifier": "",
            "buyer_name": "",
            "carrier_type": "手機載具",
            "carrier_no": mobile_carrier,
            "donate_code": "",
        }
    return {
        "invoice_type": "二聯式",
        "buyer_identifier": "",
        "buyer_name": "",
        "carrier_type": "會員載具",
        "carrier_no": member_carrier,
        "donate_code": "",
    }


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
    email = _first_match(EMAIL_RE.pattern, text)
    invoice_fields = _extract_invoice_fields(text, email)
    return BackendOrder(
        order_no=block.order_no,
        customer_name=_extract_name(text),
        phone=normalize_phone(phone),
        email=email,
        address=extract_address_from_text(text),
        amount=_extract_money(text),
        payway=_extract_payway(text),
        order_status=_extract_order_status(text),
        paid_status=_extract_paid_status(text),
        invoice_no=_extract_invoice_no(text, block.order_no),
        invoice_type=invoice_fields["invoice_type"],
        buyer_identifier=invoice_fields["buyer_identifier"],
        buyer_name=invoice_fields["buyer_name"],
        carrier_type=invoice_fields["carrier_type"],
        carrier_no=invoice_fields["carrier_no"],
        donate_code=invoice_fields["donate_code"],
        service_date=service_date,
        service_time=service_time,
        items=_extract_items(block.lines),
        raw_text=text,
        raw_lines=block.lines,
        edit_url=block.edit_url,
        purchase_id=block.purchase_id,
    )


def _form_fields_from_html(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    fields: dict[str, str] = {}
    for item in soup.find_all(["input", "textarea", "select"]):
        name = item.get("name")
        if not name:
            continue
        if item.name == "select":
            selected = item.find("option", selected=True) or item.find("option")
            if selected:
                fields[name] = (selected.get("value") or selected.get_text(" ", strip=True) or "").strip()
                fields[f"{name}__text"] = selected.get_text(" ", strip=True)
        elif item.name == "textarea":
            fields[name] = item.get_text("\n", strip=True)
        else:
            item_type = str(item.get("type") or "").lower()
            if item_type in {"radio", "checkbox"} and not item.has_attr("checked"):
                continue
            fields[name] = str(item.get("value") or "").strip()
    return fields


def _field_value(fields: dict[str, str], *names: str) -> str:
    lowered = {key.lower(): value for key, value in fields.items()}
    for name in names:
        value = fields.get(name) or lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _invoice_fields_from_codes(fields: dict[str, str], email: str) -> dict[str, str] | None:
    invoice_type = _field_value(fields, "invoice_type", "invoiceType")
    carrier_type_id = _field_value(fields, "carrier_type_id", "carrierTypeId")
    carrier_info = _field_value(fields, "carrier_info", "carrierInfo")
    company_title = _field_value(fields, "company_title", "companyTitle")
    company_no = _field_value(fields, "company_no", "companyNo")
    donate_code = _field_value(fields, "donate_code", "donateCode", "donatevat")

    if invoice_type == "3" or company_no:
        return {
            "invoice_type": "三聯式",
            "buyer_identifier": company_no,
            "buyer_name": company_title,
            "carrier_type": "紙本",
            "carrier_no": "",
            "donate_code": "",
        }
    if invoice_type == "1" or donate_code:
        return {
            "invoice_type": "二聯式",
            "buyer_identifier": "",
            "buyer_name": "",
            "carrier_type": "捐贈",
            "carrier_no": "",
            "donate_code": donate_code or "8585",
        }
    if invoice_type == "2":
        carrier_labels = {
            "1": "會員載具",
            "2": "手機載具",
            "3": "自然人憑證",
            "4": "紙本",
        }
        carrier_type = carrier_labels.get(carrier_type_id, "會員載具")
        carrier_no = carrier_info
        if carrier_type == "會員載具":
            carrier_no = carrier_info or email
        elif carrier_type == "紙本":
            carrier_no = ""
        return {
            "invoice_type": "二聯式",
            "buyer_identifier": "",
            "buyer_name": "",
            "carrier_type": carrier_type,
            "carrier_no": carrier_no,
            "donate_code": "",
        }
    return None


def hydrate_order_from_edit_page(session: LemonBackendSession, order: BackendOrder) -> BackendOrder:
    if not (order.edit_url or order.purchase_id):
        return order

    path = order.edit_url or f"/purchase/edit/{order.purchase_id}"
    kwargs: dict[str, Any] = {}
    if not order.edit_url and order.order_no:
        kwargs["params"] = {"orderNo": order.order_no}

    try:
        response = session.get(path, **kwargs)
        response.raise_for_status()
    except Exception:
        return order

    fields = _form_fields_from_html(response.text)
    text = BeautifulSoup(response.text or "", "html.parser").get_text("\n", strip=True)
    invoice_fields = _invoice_fields_from_codes(fields, order.email) or _extract_invoice_fields(text, order.email)

    payway_code = _field_value(fields, "payway")
    payway_map = {"1": "信用卡", "2": "ATM", "3": "儲值金"}
    if payway_code in payway_map:
        order.payway = payway_map[payway_code]
    if invoice_fields:
        order.invoice_type = invoice_fields["invoice_type"] or order.invoice_type
        order.buyer_identifier = invoice_fields["buyer_identifier"] or order.buyer_identifier
        order.buyer_name = invoice_fields["buyer_name"] or order.buyer_name
        order.carrier_type = invoice_fields["carrier_type"] or order.carrier_type
        order.carrier_no = invoice_fields["carrier_no"] or order.carrier_no
        order.donate_code = invoice_fields["donate_code"] or order.donate_code
    order.extra["edit_page_loaded"] = True
    return order


def parse_purchase_list_page(html: str, base_url: str = "") -> list[BackendOrder]:
    json_orders = [
        _order_from_purchase_data(item, base_url)
        for item in _extract_purchase_list_data(html)
        if isinstance(item, dict) and _text_value(item.get("order_no"))
    ]
    if json_orders:
        seen: dict[str, BackendOrder] = {}
        for order in json_orders:
            seen[order.order_no] = order
        return list(seen.values())
    return [parse_order_block(block) for block in extract_order_cards_from_purchase_html(html, base_url)]


def search_order(session: LemonBackendSession, order_no: str) -> list[BackendOrder]:
    target = str(order_no or "").strip()
    response = get_purchase_page(session, purchase_params(orderNo=target))
    return [order for order in parse_purchase_list_page(response.text, session.base_url) if order.order_no == target]


def get_order(session: LemonBackendSession, order_no: str) -> BackendOrder | None:
    matches = search_order(session, order_no)
    return hydrate_order_from_edit_page(session, matches[0]) if matches else None


def search_orders_by_phone(session: LemonBackendSession, phone: str) -> list[BackendOrder]:
    response = get_purchase_page(session, purchase_params(phone=normalize_phone(phone)))
    return parse_purchase_list_page(response.text, session.base_url)
