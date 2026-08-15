from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def _normalize_month(value: str) -> str:
    """把「2026/08」「2026.08」「2026-08」等格式統一成 YYYYMM 方便比對。"""
    digits = re.sub(r"\D", "", value)
    return digits[:6]


def _parse_bank_code(value: str) -> str:
    """從「807(永豐銀行)」這種格式取出開頭的銀行代碼。"""
    match = re.match(r"\s*(\d+)", value.strip())
    return match.group(1) if match else value.strip()


def pending_supply_purchases(values: list[list[str]], month: str) -> list[dict[str, object]]:
    """Return 清潔用品採購 rows for the given 採購月份 (B 欄), grouped by 匯款銀行＋帳號（N／O 欄）.

    篩選 B 欄符合指定月份、且 N／O 欄皆非空白的列；N／O 欄相同的列會合併成
    一筆轉帳，金額為這些列 K 欄（進貨總金額）加總。
    """
    target_month = _normalize_month(month)
    if not target_month:
        raise ValueError("採購月份格式不正確")

    groups: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []

    for sheet_row, row in enumerate(values[1:], start=2):
        row_month = _normalize_month(_cell(row, 1))
        if row_month != target_month:
            continue
        bank_text = _cell(row, 13)
        account_text = _cell(row, 14)
        if not bank_text or not account_text:
            continue
        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 10))
        if not amount_text:
            continue
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            continue
        if amount <= 0:
            continue

        key = (bank_text, account_text)
        if key not in groups:
            groups[key] = {
                "sheet_row": sheet_row,
                "rows": [sheet_row],
                "supplier": _cell(row, 5),
                "bank_text": bank_text,
                "account_number": re.sub(r"\D", "", account_text),
                "bank_code": _parse_bank_code(bank_text),
                "amount": Decimal(0),
            }
            order.append(key)
        else:
            groups[key]["rows"].append(sheet_row)
        groups[key]["amount"] += amount

    result: list[dict[str, object]] = []
    for key in order:
        group = groups[key]
        amount = group["amount"]
        normalized_amount = (
            str(int(amount)) if amount == amount.to_integral() else format(amount, "f")
        )
        result.append(
            {
                "sheet_row": group["sheet_row"],
                "rows": group["rows"],
                "supplier": group["supplier"],
                "bank_code": group["bank_code"],
                "account_number": group["account_number"],
                "amount": normalized_amount,
            }
        )
    return result
