"""【整合執行】會計事務所費用確認：盤點本期項目 → 填／改金額、勾選（可全選）→ 直接排富邦轉帳給勤信。

更新紀錄：
  2026-11-09 Jenny：勾選的合併成一筆，排本機 Agent 從台北富邦轉給勤信（013 國泰世華 0000240035015567）；
                    轉完請款記錄／台北記已付款＋付款日期，台中的費用另記請款記錄／台中待付款（付給台北，尾數 3320）。
                    記帳費每個單月都列出，1、7 月以外預設不勾。
"""
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
               "5、9 月營所稅；台北／台中半年度記帳費（通常 1、7 月，其他單月也列出、不預設勾）；會計師簽證費（通常 3 月）。"
               "營業稅、營所稅要填金額；記帳費有調整可直接改金額。"
               f"勾選的合併成一筆，由本機 Agent 從台北富邦轉給勤信（{batch.PAYEE_BANK_CODE} 國泰世華 {batch.PAYEE_ACCOUNT}），"
               "富邦核對後手機核准。轉完自動寫請款記錄／台北（已付款＋付款日期）；"
               "台中營業稅、台中記帳費另記請款記錄／台中待付款（付給台北富邦尾數 3320，到【整合執行】富邦內部請款付）。")
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    items = scan_button(PREFIX, period, lambda p: batch.scan(_checked_period(p), log=log))
    if items is None:
        return
    def run(selected: list[dict]) -> None:
        batch.queue_transfer(period, selected, create_task, created_by=username or batch.TOOL_NAME, log=log)
        st.success("已排入本機 Agent：台北富邦 → 勤信，合併一筆 NT$ "
                   f"{sum(int(i['amount']) for i in selected):,}。請到富邦核對後手機核准，轉完會自動寫請款記錄，進度看下方。")

    render_request_items(PREFIX, items, log, tool_name=batch.TOOL_NAME, editable_amount=True,
                         on_run=run, run_label="富邦轉帳：勾選的 {n} 筆合併一筆轉給勤信")
