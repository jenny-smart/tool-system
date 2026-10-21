from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


STATUS_CONFIG = {
    "待扣儲值金": {
        "action": "扣款",
        "amount_column": 13,
        "time_column": 12,
        "suffix": "已扣",
        "completed_status": "已扣儲值金",
    },  # N 金額、M 時間
    "待返儲值金": {
        "action": "入帳",
        "amount_column": 18,
        "time_column": 28,
        "suffix": "已返",
        "completed_status": "已返儲值金",
    },  # S 金額、AC 時間
    # VIP待退券：退款金額（S）不一定等於儲值金，金額以儲值金歷程頁的餘額為準（見 zero_out_steps），
    # 儲值金及購物金都扣到 0；M 填歸零時間，B 欄維持 VIP待退券（之後還要開折讓單、退款）。
    "VIP待退券": {
        "action": "扣款",
        "time_column": 12,
        "suffix": "VIP退券歸零",
        "completed_status": "VIP待退券",
        "zero_out": True,
    },  # M 時間
}
ZERO_OUT_AMOUNT = "全部歸零"


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
        zero_out = bool(config.get("zero_out"))
        if zero_out:
            if _cell(row, int(config["time_column"])):
                continue  # M 已有歸零時間
            amount = ZERO_OUT_AMOUNT
        else:
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
                "completed_status": config["completed_status"],
                "time_column": int(config["time_column"]) + 1,
                "completed_at": _cell(row, int(config["time_column"])),
                "zero_out": zero_out,
                "refund_amount": normalize_amount(_cell(row, 18)) if zero_out else "",  # S
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


def parse_balances(text: str) -> tuple[int, int] | None:
    """儲值金歷程頁：「儲值金餘額：17,600」「購物金餘額：2,500」→ (17600, 2500)；讀不到回傳 None。"""
    values = []
    for label in ("儲值金餘額", "購物金餘額"):
        match = re.search(rf"(?<!及){label}\s*[:：]\s*(-?[\d,]+)", str(text or ""))
        if not match:
            return None
        values.append(int(match.group(1).replace(",", "")))
    return values[0], values[1]


def zero_out_steps(stored: int, shopping: int, refund_amount: str = "") -> list[tuple[str, int]]:
    """VIP待退券歸零要扣的每一筆（備註後綴, 金額），全部用「扣款／異動費」。

    後台扣款先扣儲值金、不足再扣購物金：
      - 退款金額大於儲值金：第一筆扣退款金額，第二筆扣剩下的餘額（購物金）。
      - 其他：第一筆扣儲值金餘額，第二筆扣購物金餘額。
    """
    total = stored + shopping
    if total <= 0:
        return []
    try:
        refund = int(float(refund_amount or 0))
    except ValueError:
        refund = 0
    if refund > stored:
        first = min(refund, total)
        steps = [("VIP退券扣退款金額", first)]
    else:
        first = stored
        steps = [("VIP退券儲值金歸零", first)] if first else []
    if total - first:
        steps.append(("VIP退券剩餘餘額歸零", total - first))
    return steps
