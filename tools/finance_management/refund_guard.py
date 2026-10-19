"""退款前確認：有開發票（X 欄原發票號碼）的列，必須已有折讓單號（AB 欄）才退款。

藍新信用卡退款、富邦 ATM 退款共用。每一筆退款前重新讀一次該列，
折讓單沒開成功（或沒回填）就跳過、不退款，避免只退款沒折讓。沒開發票（X 空白）的列不需要折讓單。
"""
from __future__ import annotations

from typing import Callable

X, AB = 23, 27  # 清潔異動：X 原發票號碼、AB 折讓單號


def missing_allowance(values: list[list[object]], sheet_row: int) -> bool:
    row = values[sheet_row - 1] if 0 < sheet_row <= len(values) else []
    cell = lambda i: str(row[i]).strip() if i < len(row) else ""  # noqa: E731
    return bool(cell(X)) and not cell(AB)


def ready_for_refund(worksheet, sheet_row: int, log: Callable[[str], None] = print) -> bool:
    """退款前重新讀該列；缺折讓單號就回傳 False 並提示。"""
    if missing_allowance(worksheet.get_all_values(), sheet_row):
        log(f"⚠️ 第 {sheet_row} 列有原發票但沒有折讓單號（AB），未退款；請先完成折讓單再退款")
        return False
    return True
