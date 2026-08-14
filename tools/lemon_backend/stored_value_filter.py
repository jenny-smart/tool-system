from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


STATUS_CONFIG = {
    "待扣儲值金": {
        "action": "扣款", "amount_column": 13, "time_column": 12, "suffix": "已扣"
    },  # N 金額、M 時間
    "待退儲值金": {
        "action": "入帳", "amount_column": 18, "time_column": 28, "suffix": "已返"
    },  # S 金額、AC 時間
}


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def normalize_amount(value: str) -> str:
    normalized = re.sub(r"[^0-9.]", "", str(value or ""))
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return ""
    if amount <= 0:
        return ""
    return str(int(amount)) if amount == amount.to_integral() else format(amount, "f")


def pending_stored_value_adjustments(values: list[list[str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        status = _cell(row, 1)  # B
        config = STATUS_CONFIG.get(status)
        if not config:
            continue
        order_no = _cell(row, 6)  # G
        note = _cell(row, 10)  # K
        amount = normalize_amount(_cell(row, int(config["amount_column"])))
        if not order_no or not amount:
            continue
        rows.append(
            {
                "sheet_row": sheet_row,
                "status": status,
                "order_no": order_no,
                "note": note,
                "amount": amount,
                "action": config["action"],
                "suffix": config["suffix"],
                "time_column": int(config["time_column"]) + 1,
                "completed_at": _cell(row, int(config["time_column"])),
            }
        )
    return rows


def build_note(note: str, date_text: str, suffix: str) -> str:
    marker = f"{date_text}{suffix}"
    note = str(note or "").strip()
    if not note:
        return marker
    if marker in note:
        return note
    separator = "" if note.endswith(("，", ",", "、", " ", "\n")) else "，"
    return f"{note}{separator}{marker}"
