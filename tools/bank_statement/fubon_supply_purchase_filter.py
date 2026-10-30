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


def _is_blank(value: str) -> bool:
    return not value or value.upper().startswith("#N/A")


def is_paid(row: list[str]) -> bool:
    """Q 欄（匯款日期）或 S 欄（請款日期）有值＝已付款。"""
    return bool(_cell(row, 16) or _cell(row, 18))


def pending_supply_purchases(values: list[list[str]], month: str, merge: bool = True) -> list[dict[str, object]]:
    """Return 清潔用品採購 rows for the given 採購月份 (B 欄), grouped by 匯款銀行＋帳號（N／O 欄）.

    篩選 B 欄符合指定月份、Q 欄（匯款日期）與 S 欄（請款日期）都空白、
    且 N／O 欄皆有實際內容（不是空白或 #N/A）的列；N／O 欄相同的未匯款列
    會合併成一筆轉帳，金額為這些列 K 欄（進貨總金額）加總。
    Q 或 S 欄已有日期的列視為已付款，一律不再放入匯款名單。
    merge=False 時不合併，每一列各自一筆。
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
        if is_paid(row):
            continue
        bank_text = _cell(row, 13)
        account_text = _cell(row, 14)
        if _is_blank(bank_text) or _is_blank(account_text):
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

        key = (bank_text, account_text) if merge else (bank_text, account_text, sheet_row)
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


def split_groups(values: list[list[str]], month: str, groups: list[dict[str, object]],
                 split_rows) -> list[dict[str, object]]:
    """把 sheet_row 在 split_rows 裡的合併筆拆回每一列各自轉帳（取消勾選「合併」）；其他維持原樣。"""
    split_rows = {int(r) for r in split_rows}
    if not split_rows:
        return groups
    singles = {int(item["sheet_row"]): item for item in pending_supply_purchases(values, month, merge=False)}
    result: list[dict[str, object]] = []
    for group in groups:
        if int(group["sheet_row"]) in split_rows:
            result.extend(singles[int(r)] for r in group["rows"] if int(r) in singles)
        else:
            result.append(group)
    return sorted(result, key=lambda m: int(m["sheet_row"]))
