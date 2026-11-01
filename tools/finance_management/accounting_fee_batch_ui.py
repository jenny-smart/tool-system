"""【整合執行】會計事務所費用確認：盤點本期項目 → 填／改金額、勾選（可全選）→ 新增到請款記錄／台北。"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import streamlit as st

from tools.finance_management import accounting_fee_batch as batch
from tools.finance_management.fixed_expense_batch_ui import _checked_period, render_request_items, scan_button

PREFIX = "accounting_fee_batch"


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每單月 10 日左右：台北／台中前 2 個月營業稅、當月／下月辦公室租金代扣繳 8,800×2、二代健保 1,857×2；"
               "5、9 月營所稅；1、7 月半年度記帳費；會計師簽證費（通常 3 月）。"
               "營業稅、營所稅要填金額；記帳費有調整可直接改金額。全部記在請款記錄／台北。")
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    items = scan_button(PREFIX, period, lambda p: batch.scan(_checked_period(p), log=log))
    if items is None:
        return
    render_request_items(PREFIX, items, log, tool_name=batch.TOOL_NAME, editable_amount=True)
