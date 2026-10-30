from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_atm_refunds(values: list[list[str]]) -> list[dict[str, object]]:
    """Return 清潔異動 rows where B=待退款／VIP待退券 and R=ATM.

    Sheet columns used by the transfer flow: H customer, P bank code,
    Q destination account, and T refund amount. K (後台備註) is included
    for display only.
    """
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 1)
        payway = _cell(row, 17).upper()
        bank_code = re.sub(r"\D", "", _cell(row, 15))
        account_number = re.sub(r"\D", "", _cell(row, 16))
        amount_text = re.sub(r"[^0-9.]", "", _cell(row, 19))
        if status not in ("待退款", "VIP待退券") or payway != "ATM":
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
                "status": status,
                "order_no": _cell(row, 6),
                "customer": _cell(row, 7),
                "note": _cell(row, 10),
                "bank_code": bank_code.zfill(3),
                "account_number": account_number,
                "amount": normalized_amount,
            }
        )
    return result


def merge_by_account(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """同一轉入帳號（P 銀行代碼＋Q 帳號都相同）合併成一筆轉帳，金額加總。

    合併後 members 保留每一列原始資料，轉帳完成後每一列各自回寫 AC／B 欄；只有一列的維持原樣。
    """
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for item in items:
        groups.setdefault((str(item["bank_code"]), str(item["account_number"])), []).append(item)
    merged: list[dict[str, object]] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        members = sorted(members, key=lambda m: int(m["sheet_row"]))
        total = sum(Decimal(str(m["amount"])) for m in members)
        merged.append({
            **members[0],
            "rows": [int(m["sheet_row"]) for m in members],
            "members": members,
            "customer": "、".join(dict.fromkeys(str(m["customer"]) for m in members)),
            "amount": str(int(total)) if total == total.to_integral() else format(total, "f"),
        })
    return sorted(merged, key=lambda m: int(m["sheet_row"]))
