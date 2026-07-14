from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import re
from typing import Any
import xml.etree.ElementTree as ET

import requests

from .config import AREA_ENV, get_secret, normalize_area
from .models import InvoicePayload, InvoiceResult, format_amount


DEFAULT_LEMON_API_BASE_URL = "https://api.lemonclean.com.tw"
DEFAULT_EI_SOAP_ENDPOINT = "https://www.ei.com.tw/InvoiceB2C/InvoiceAPI"
EI_SOAP_NAMESPACE = "http://webservice.cetustek.com/"
INVOICE_NO_RE = re.compile(r"^[A-Z]{2}\d{8}$")

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


def get_ei_soap_endpoint() -> str:
    configured = get_secret("EI_SOAP_ENDPOINT_URL")
    if configured:
        return configured.replace("?wsdl", "").rstrip("/")
    return DEFAULT_EI_SOAP_ENDPOINT


def get_ei_rent_id(area: str) -> str:
    area_key = normalize_area(area)
    meta = AREA_ENV[area_key]
    prefix = meta["userid"].replace("_EI_USERID", "")
    rent_id = get_secret(f"{prefix}_EI_RENTID") or get_secret(meta["userid"])
    rent_id = str(rent_id or "").strip()
    if not rent_id:
        raise RuntimeError(f"{meta['label']} EI rentid 尚未設定，請設定 {prefix}_EI_RENTID")
    return rent_id


def _soap_return(response_text: str) -> str:
    root = ET.fromstring(response_text.encode("utf-8"))
    for element in root.iter():
        if element.tag.endswith("return"):
            return (element.text or "").strip()
    return ""


def _soap_call(operation: str, params: dict[str, Any], *, timeout: int = 30) -> str:
    body = "".join(
        f"<{name}>{escape(str(value if value is not None else ''))}</{name}>"
        for name, value in params.items()
    )
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:web="{EI_SOAP_NAMESPACE}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        f"<web:{operation}>{body}</web:{operation}>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    response = requests.post(
        get_ei_soap_endpoint(),
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"{EI_SOAP_NAMESPACE}InvoiceAPI/{operation}Request",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return _soap_return(response.text)


def _ymd_slash(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    text = str(value or "").strip()
    if not text:
        return date.today().strftime("%Y/%m/%d")
    return text.replace("-", "/")[:10]


def _payway_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"2", "ATM"} or "ATM" in text:
        return "2"
    return "3"


def _donate_mark(payload: InvoicePayload) -> str:
    if str(payload.donate or "").strip() == "1" or str(payload.donatevat or "").strip():
        return "1"
    if str(payload.buyer_identifier or "").strip():
        return "2"
    if not str(payload.carriertype or "").strip():
        return "2"
    return "0"


def _xml_tag(name: str, value: Any = "") -> str:
    return f"<{name}>{escape(str(value if value is not None else ''))}</{name}>"


def build_invoice_xml(payload: InvoicePayload) -> str:
    items = list(payload.items or [])
    detail_items = []
    for item in items:
        detail_items.append(
            "<ProductItem>"
            + _xml_tag("ProductionCode", item.goodcode or "CLEAN")
            + _xml_tag("Description", item.goodname or "清潔服務")
            + _xml_tag("Quantity", format_amount(item.quantity))
            + _xml_tag("Unit", item.unit or "次")
            + _xml_tag("UnitPrice", format_amount(item.unitprice))
            + "</ProductItem>"
        )
    if not detail_items:
        detail_items.append(
            "<ProductItem>"
            + _xml_tag("ProductionCode", "CLEAN")
            + _xml_tag("Description", "清潔服務")
            + _xml_tag("Quantity", "1")
            + _xml_tag("Unit", "次")
            + _xml_tag("UnitPrice", format_amount(payload.saleamount))
            + "</ProductItem>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Invoice XSDVersion="2.8">'
        + _xml_tag("OrderId", payload.orderid)
        + _xml_tag("OrderDate", _ymd_slash(payload.orderdate))
        + _xml_tag("BuyerIdentifier", payload.buyer_identifier)
        + _xml_tag("BuyerName", payload.buyer_name)
        + _xml_tag("BuyerAddress", payload.buyer_address)
        + _xml_tag("BuyerPersonInCharge")
        + _xml_tag("BuyerTelephoneNumber", payload.buyer_phone)
        + _xml_tag("BuyerFacsimileNumber")
        + _xml_tag("BuyerEmailAddress", payload.buyer_emailaddress)
        + _xml_tag("BuyerCustomerNumber")
        + _xml_tag("DonateMark", _donate_mark(payload))
        + _xml_tag("InvoiceType", payload.invoicetype or "07")
        + _xml_tag("CarrierType", payload.carriertype)
        + _xml_tag("CarrierId1", payload.carrierid1)
        + _xml_tag("CarrierId2", payload.carrierid2)
        + _xml_tag("NPOBAN", payload.donatevat)
        + _xml_tag("TaxType", payload.taxtype or "1")
        + _xml_tag("TaxRate", payload.rate or "0.05")
        + _xml_tag("PayWay", _payway_code(payload.payway))
        + _xml_tag("Remark", payload.mainremark)
        + "<Details>"
        + "".join(detail_items)
        + "</Details>"
        + "</Invoice>"
    )


def get_invoice_error_message(code: str) -> str:
    normalized = re.sub(r"[_:].*$", "", str(code or "").strip())
    messages = {
        "InValid": "無效 IP，請通知系統商",
        "A2": "所有折讓金額加總不能大於原發票金額",
        "A3": "發票號碼不存在",
        "A4": "發票號碼已經被作廢",
        "A5": "折讓單已經上傳",
        "A6": "折讓總金額須大於零",
        "S4": "未取得發票號碼",
        "S5": "發票號碼已使用完畢",
        "S7": "訂單號碼已存在，若需重開請先作廢原發票號碼",
        "S8": "開立的總金額為負值",
        "nodata": "查無資料",
    }
    return messages.get(normalized) or messages.get(str(code or "").strip(), "未知錯誤")


def create_invoice_by_soap(
    payload: InvoicePayload,
    *,
    rent_id: str | None = None,
    dry_run: bool = False,
    timeout: int = 30,
) -> InvoiceResult:
    resolved_rent_id = str(rent_id or get_ei_rent_id(payload.area)).strip()
    invoice_xml = build_invoice_xml(payload)
    soap_payload = {
        "operation": "CreateInvoiceV3",
        "rentid": resolved_rent_id,
        "invoicexml": invoice_xml,
    }
    if dry_run:
        return InvoiceResult(
            success=True,
            dry_run=True,
            message="Dry-run only. EI SOAP CreateInvoiceV3 was not called.",
            payload=soap_payload,
        )

    returned = _soap_call(
        "CreateInvoiceV3",
        {"invoicexml": invoice_xml, "hastax": "1", "rentid": resolved_rent_id},
        timeout=timeout,
    )
    success = bool(INVOICE_NO_RE.fullmatch(returned))
    return InvoiceResult(
        success=success,
        dry_run=False,
        message="EI SOAP CreateInvoiceV3 completed." if success else "EI SOAP CreateInvoiceV3 failed.",
        payload=soap_payload,
        invoice_no=returned if success else "",
        raw_text=returned,
        error="" if success else f"{returned} {get_invoice_error_message(returned)}".strip(),
    )


def query_invoice_number_by_order_id(
    order_id: str,
    *,
    area: str = "taipei",
    rent_id: str | None = None,
    timeout: int = 30,
) -> str:
    resolved_rent_id = str(rent_id or get_ei_rent_id(area)).strip()
    returned = _soap_call(
        "QueryInvoiceNumberByOrderid",
        {"Orderid": str(order_id or "").strip(), "rentid": resolved_rent_id},
        timeout=timeout,
    )
    if returned == "nodata":
        return ""
    return returned if INVOICE_NO_RE.fullmatch(returned) else ""


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
