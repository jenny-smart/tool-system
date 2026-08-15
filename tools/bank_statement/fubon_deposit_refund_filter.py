from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_deposit_refunds(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 工具包押金退款 rows where H>0 and S is blank.

    轉帳金額為 R 欄；Q 欄同時作為給自己／給對方的留言。
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        if _cell(row, 18):  # S 欄已有值，代表已處理過
            continue
        deposit_text = re.sub(r"[^0-9.\-]", "", _cell(row, 7))
        if not deposit_text:
            continue
        try:
            deposit = Decimal(deposit_text)
        except InvalidOperation:
            continue
        if deposit <= 0:
            continue

        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 17))
        memo = _cell(row, 16)
        if not amount_text:
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
                "memo": memo,
                "amount": normalized_amount,
            }
        )
    return result
