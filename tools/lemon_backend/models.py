from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PurchaseBlock:
    order_no: str
    lines: list[str] = field(default_factory=list)
    edit_url: str = ""
    purchase_id: str = ""

    @property
    def raw_text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class BackendOrder:
    order_no: str = ""
    customer_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    amount: str = ""
    payway: str = ""
    paid_status: str = ""
    invoice_no: str = ""
    service_date: str = ""
    service_time: str = ""
    items: list[str] = field(default_factory=list)
    raw_text: str = ""
    raw_lines: list[str] = field(default_factory=list)
    edit_url: str = ""
    purchase_id: str = ""
    source: str = "purchase"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_no": self.order_no,
            "customer_name": self.customer_name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "amount": self.amount,
            "payway": self.payway,
            "paid_status": self.paid_status,
            "invoice_no": self.invoice_no,
            "service_date": self.service_date,
            "service_time": self.service_time,
            "items": self.items,
            "raw_text": self.raw_text,
            "raw_lines": self.raw_lines,
            "edit_url": self.edit_url,
            "purchase_id": self.purchase_id,
            "source": self.source,
            "extra": self.extra,
        }


@dataclass
class BackendResult:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
