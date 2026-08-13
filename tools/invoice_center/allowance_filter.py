from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _amount(value: object) -> str:
    text = str(value or "").replace(",", "").replace("NT$", "").replace("$", "").strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return ""
    return str(int(number)) if number == number.to_integral() else format(number, "f")


def pending_allowances(values: list[list[object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for sheet_row, row in enumerate(values[1:], start=2):
        cells = [str(value or "").strip() for value in row] + [""] * 28
        if cells[1] != "待退款" or not cells[23] or not _amount(cells[25]) or cells[27]:
            continue
        result.append({
            "sheet_row": sheet_row,
            "order_no": cells[6],
            "invoice_no": cells[23],
            "refund_amount": _amount(cells[18]),
            "untaxed_amount": _amount(cells[25]),
        })
    return result
