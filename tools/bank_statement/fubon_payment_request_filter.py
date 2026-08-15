from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_payment_requests(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 請款報表 rows where A=待付款.

    轉入帳號依 G 欄名字比對富邦已存的常用轉入帳號，轉帳金額為 F 欄。
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 0)
        if status != "待付款":
            continue
        name = _cell(row, 6)
        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 5))
        if not name or not amount_text:
            continue
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            continue
        if amount <= 0:
            continue
        normalized_amount = (
            str(int(amount)) if amount == amount.to_integral() else format(amount, "f")
        )
        result.append(
            {
                "sheet_row": sheet_row,
                "name": name,
                "amount": normalized_amount,
            }
        )
    return result
