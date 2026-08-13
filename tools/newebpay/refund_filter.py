from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_credit_card_refunds(values: list[list[str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row_number, row in enumerate(values[1:], start=2):
        status, order_no, payway, amount = _cell(row, 1), _cell(row, 6), _cell(row, 17), _cell(row, 18)
        if status != "待退款" or payway != "信用卡" or not order_no or not amount:
            continue
        normalized = re.sub(r"[^0-9.]", "", amount)
        try:
            parsed_amount = Decimal(normalized)
        except InvalidOperation:
            continue
        if parsed_amount > 0:
            rows.append({"sheet_row": row_number, "order_no": order_no, "amount": normalized})
    return rows
