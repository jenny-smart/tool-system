from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExpressPayment:
    merchant: str
    transaction_no: str
    merchant_order_no: str
    product: str
    payment_method: str
    status_and_time: str
    payer: str
    phone: str
    amount: int | float

    def as_row(self) -> list[str | int | float]:
        return [
            self.merchant,
            "\n".join(filter(None, (self.transaction_no, self.merchant_order_no))),
            self.product,
            self.payment_method,
            self.status_and_time,
            self.payer,
            self.phone,
            self.amount,
        ]


def amount_value(value: str) -> int | float:
    match = re.search(r"(?:NT\$)?\s*([\d,]+(?:\.\d+)?)", value)
    if not match:
        raise RuntimeError(f"無法解析藍新金額：{value}")
    normalized = match.group(1).replace(",", "")
    return float(normalized) if "." in normalized else int(normalized)


def new_rows(
    payments: list[ExpressPayment], existing: set[str]
) -> list[list[str | int | float]]:
    rows: list[list[str | int | float]] = []
    seen = set(existing)
    for payment in payments:
        if payment.transaction_no in seen:
            continue
        rows.append(payment.as_row())
        seen.add(payment.transaction_no)
    return rows
