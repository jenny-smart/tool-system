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
                "rows": [sheet_row],
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


def merge_by_payee(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """同一收款對象（H 欄收款對象＋I 欄收款帳號都相同）合併成一筆轉帳，金額加總。

    合併後的 rows 列出所有原始列號，轉帳完成後每一列都要回填；只有一列的維持原樣。
    """
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for item in items:
        account = str(item.get("account_number") or "") or str(item.get("account_text") or "")
        groups.setdefault((str(item["name"]), account), []).append(item)
    merged: list[dict[str, object]] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        rows = sorted(int(m["sheet_row"]) for m in members)
        total = sum(Decimal(str(m["amount"])) for m in members)
        merged.append({
            **members[0],
            "sheet_row": rows[0],
            "rows": rows,
            "amount": str(int(total)) if total == total.to_integral() else format(total, "f"),
            "memo": f"合併 {len(rows)} 筆（第 {'、'.join(map(str, rows))} 列）",
        })
    return sorted(merged, key=lambda m: int(m["sheet_row"]))
