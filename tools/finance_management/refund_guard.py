"""退款前確認：有開發票（X 欄原發票號碼）的列，必須已有折讓單號（AB 欄）才退款。

藍新信用卡退款、富邦 ATM 退款共用。登入網站前先用 ready_items 篩掉缺折讓單號的列
（全部都缺就不登入）；每一筆退款前再重新讀一次該列，
折讓單沒開成功（或沒回填）就跳過、不退款，避免只退款沒折讓。沒開發票（X 空白）的列不需要折讓單。
VIP待退券另外要先把儲值金及購物金歸零（M 欄有歸零時間）才退款；退款完成 B 欄改為 VIP已退券。
"""
from __future__ import annotations

from typing import Callable

B, M, X, AB = 1, 12, 23, 27  # 清潔異動：B 狀態、M 時間、X 原發票號碼、AB 折讓單號
REFUND_STATUSES = {"待退款": "已退款", "VIP待退券": "VIP已退券"}  # 待退狀態 → 退款完成後 B 欄狀態


def refunded_status(status: str) -> str:
    return REFUND_STATUSES.get(str(status or "").strip(), "已退款")


def _row(values: list[list[object]], sheet_row: int) -> Callable[[int], str]:
    row = values[sheet_row - 1] if 0 < sheet_row <= len(values) else []
    return lambda i: str(row[i]).strip() if i < len(row) else ""


def missing_allowance(values: list[list[object]], sheet_row: int) -> bool:
    cell = _row(values, sheet_row)
    return bool(cell(X)) and not cell(AB)


def blocked_reason(values: list[list[object]], sheet_row: int) -> str:
    """還不能退款的原因；可以退款回傳空字串。"""
    cell = _row(values, sheet_row)
    if cell(B) == "VIP待退券" and not cell(M):
        return f"第 {sheet_row} 列 VIP待退券尚未把儲值金及購物金歸零（M 欄空白），未退款；請先歸零再退款"
    if missing_allowance(values, sheet_row):
        return f"第 {sheet_row} 列有原發票但沒有折讓單號（AB），未退款；請先完成折讓單再退款"
    return ""


def ready_for_refund(worksheet, sheet_row: int, log: Callable[[str], None] = print) -> bool:
    """退款前重新讀該列；缺折讓單號（或 VIP 尚未歸零）就回傳 False 並提示。"""
    reason = blocked_reason(worksheet.get_all_values(), sheet_row)
    if reason:
        log(f"⚠️ {reason}")
        return False
    return True


def ready_items(values: list[list[object]], items: list[dict], log: Callable[[str], None] = print) -> list[dict]:
    """登入前先篩：缺折讓單號（或 VIP 尚未歸零）的列提示後剔除，回傳可退款的列。"""
    ready = []
    for item in items:
        reason = blocked_reason(values, int(item["sheet_row"]))
        if reason:
            log(f"⚠️ {reason}")
        else:
            ready.append(item)
    return ready
