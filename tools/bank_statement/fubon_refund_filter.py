from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_atm_refunds(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 清潔異動 rows where B=待退款 and R=ATM.

    Sheet columns used by the transfer flow: H customer, P bank code,
    Q destination account, and T refund amount.
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 1)
        payway = _cell(row, 17).upper()
        bank_code = re.sub(r"\D", "", _cell(row, 15))
        account_number = re.sub(r"\D", "", _cell(row, 16))
        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 19))
        if status != "待退款" or payway != "ATM":
            continue
        if not bank_code or not account_number or not amount_text:
            continue
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            continue
        if amount < 0:
            continue
        normalized_amount = (
            str(int(amount)) if amount == amount.to_integral() else format(amount, "f")
        )
        result.append(
            {
                "sheet_row": sheet_row,
                "order_no": _cell(row, 6),
                "customer": _cell(row, 7),
                "bank_code": bank_code.zfill(3),
                "account_number": account_number,
                "amount": normalized_amount,
            }
        )
    return result
