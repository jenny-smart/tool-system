from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_payment_requests(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 請款報表 rows where A=待付款.

    轉入帳號優先依 G 欄名字比對富邦已存的常用轉入帳號；如果常用轉入帳號
    清單裡找不到，改用 H 欄（銀行名稱）／I 欄（帳號）自行輸入。轉帳金額為 F 欄。
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 0)
        if status != "待付款":
            continue
        name = _cell(row, 6)
        bank_name = _cell(row, 7)
        account_number = re.sub(r"\D", "", _cell(row, 8))
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
                "bank_name": bank_name,
                "account_number": account_number,
                "amount": normalized_amount,
            }
        )
    return result
