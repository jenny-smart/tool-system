from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def _split_bank_and_account(value: str) -> tuple[str, str]:
    """把 I 欄「銀行名稱-帳號」拆成 (銀行代碼或名稱, 帳號)。

    格式不一定乾淨，例如 "806-21022000251012"、"玉山銀行808-01294400367"、
    "凱基銀行-60250100000118"。優先取銀行段裡的 3 碼代碼；沒有代碼才退回
    用純文字名稱比對。
    """
    text = value.strip()
    if not text:
        return "", ""
    if "-" not in text:
        return "", re.sub(r"\D", "", text)
    bank_part, _, account_part = text.rpartition("-")
    account_number = re.sub(r"\D", "", account_part)
    code_match = re.search(r"\d{3}", bank_part)
    bank_identifier = code_match.group(0) if code_match else re.sub(r"\d", "", bank_part).strip()
    return bank_identifier, account_number


def pending_payment_requests(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 請款報表 rows where A=待付款.

    轉入帳號依 H 欄（收款對象＝實際收款人，跟 G 欄請款人不一定是同一人）
    比對富邦已存的常用轉入帳號；如果常用轉入帳號清單裡找不到，改用 I 欄
    （收款帳號，格式「銀行名稱-帳號」）自行輸入。G 欄（請款人）僅供人工
    核對用，不參與比對。轉帳金額為 F 欄，E 欄（費用說明）僅供人工核對用。
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 0)
        if status != "待付款":
            continue
        memo = _cell(row, 4)
        requester = _cell(row, 6)
        payee = _cell(row, 7)
        account_text = _cell(row, 8)
        bank_identifier, account_number = _split_bank_and_account(account_text)
        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 5))
        if not payee or not amount_text:
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
                "name": payee,
                "requester": requester,
                "bank_name": bank_identifier,
                "account_number": account_number,
                "account_text": account_text,
                "amount": normalized_amount,
            }
        )
    return result
