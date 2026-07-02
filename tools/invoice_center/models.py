from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Union


MoneyValue = Union[Decimal, int, float, str]


def to_decimal(value: MoneyValue | None, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return default


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_amount(value: MoneyValue | None) -> str:
    amount = to_decimal(value)
    if amount == amount.to_integral_value():
        return str(amount.to_integral_value())
    return format(amount.normalize(), "f")


def _clean_detail_field(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text.replace("|", "/").replace("\r", " ").replace("\n", " ")


@dataclass
class InvoiceLineItem:
    goodcode: str
    goodname: str
    unit: str = "式"
    quantity: MoneyValue = "1"
    unitprice: MoneyValue = "0"
    amount: MoneyValue | None = None
    fremark: str = ""

    def amount_value(self) -> Decimal:
        if self.amount not in (None, ""):
            return to_decimal(self.amount)
        return to_decimal(self.quantity) * to_decimal(self.unitprice)

    def detail_row(self) -> str:
        parts = [
            self.goodcode,
            self.goodname,
            self.unit,
            format_amount(self.quantity),
            format_amount(self.unitprice),
            format_amount(self.amount_value()),
            self.fremark,
        ]
        return "|".join(_clean_detail_field(part) for part in parts)


@dataclass
class InvoicePayload:
    area: str = "taipei"
    orderid: str = ""
    orderdate: str = ""
    detaildata: str = ""
    buyer_identifier: str = ""
    buyer_name: str = ""
    buyer_address: str = ""
    buyer_emailaddress: str = ""
    buyer_phone: str = ""
    payway: str = ""
    mainremark: str = ""
    roundnum: int = 4
    invoicetype: str = "07"
    taxtype: str = "1"
    zerotype: str = ""
    zeroreason: str = ""
    donate: str = "0"
    hastax: str = "2"
    hasapply: str = "1"
    rate: MoneyValue = "0.05"
    carriertype: str = "EJ0011"
    carrierid1: str = ""
    carrierid2: str = ""
    donatevat: str = ""
    saleamount: MoneyValue = "0"
    taxamount: MoneyValue | None = None
    totalamount: MoneyValue | None = None
    cd: str = ""
    invid: str = ""
    invdate: str = ""
    struts_token_name: str = "token"
    token: str = ""
    ctoken: str = ""
    items: list[InvoiceLineItem] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_ei_payload(self) -> dict[str, Any]:
        from .invoice import build_add_invoice_payload

        return build_add_invoice_payload(self)


@dataclass
class InvoiceResult:
    success: bool
    dry_run: bool
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    status_code: int | None = None
    response_url: str = ""
    invoice_no: str = ""
    raw_text: str = ""
    error: str = ""
