"""清潔異動工作表：與 orders-system 服務異動同一套找法。

檔案依年度根目錄找（桃園／新竹「服務異動」，其他地區「內勤工作表單」），
分頁依名稱找「清潔異動」（桃園／新竹為唯一的「…訂單金流…異動表」），找不到就報錯；
不再讀「財務帳務設定」清潔異動列的試算表ID／GID。
"""
from __future__ import annotations

from typing import Optional

SUPPORTED_AREAS = ("台北", "台中", "桃園", "新竹", "高雄")


def _change_order():
    from tools.memo_system import change_order

    return change_order


def get_sheet_config(area: str) -> tuple[str, Optional[int]]:
    if area not in SUPPORTED_AREAS:
        raise ValueError(f"不支援的地區：{area}")
    return _change_order()._sheet_config(area)["spreadsheet_id"], None


def get_worksheet(area: str, tab_name: str = "清潔異動"):
    if area not in SUPPORTED_AREAS:
        raise ValueError(f"不支援的地區：{area}")
    return _change_order().get_worksheet(area, tab_name)
