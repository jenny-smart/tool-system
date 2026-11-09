"""【整合執行】會計事務所費用確認：盤點本期項目 → 填／改金額、勾選（可全選）→ 直接排富邦轉帳給勤信。

更新紀錄：
  2026-11-09 Jenny：按下去寫請款記錄／台北（付勤信會計事務所，富邦選約定轉入帳號）＋台中的費用另記請款記錄／台中（付台北），
                    接著直接排富邦內部請款轉帳（台北這幾列合併一筆轉勤信，轉完回填已付款＋付款日期）；
                    記帳費每個單月都列出，1、7 月以外預設不勾。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import streamlit as st

from tools.finance_management import accounting_fee_batch as batch
from tools.finance_management import fixed_expense_batch as fixed
from tools.finance_management import internal_payment_batch as payment
from tools.finance_management.fixed_expense_batch_ui import _checked_period, render_request_items, scan_button

PREFIX = "accounting_fee_batch"


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每單月 10 日左右：台北／台中前 2 個月營業稅、當月／下月辦公室租金代扣繳 8,800×2、二代健保 1,857×2；"
               "5、9 月營所稅；台北／台中半年度記帳費（通常 1、7 月，其他單月也列出、不預設勾）；會計師簽證費（通常 3 月）。"
               "營業稅、營所稅要填金額；記帳費有調整可直接改金額。"
               "按下去：全部寫進請款記錄／台北（收款對象勤信會計事務所，富邦選約定轉入帳號 "
               f"{batch.PAYEE_ACCOUNT_TEXT}），台中營業稅、台中記帳費另記請款記錄／台中（收款對象台北，尾數 3320）；"
               "接著本機 Agent 從台北富邦把台北這幾列合併一筆轉給勤信（停在確認頁，請核對後在電腦上按『確認』），"
               "轉完自動回填已付款＋付款日期、下載富邦明細。台中那幾列到【整合執行】富邦內部請款付。")
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    items = scan_button(PREFIX, period, lambda p: batch.scan(_checked_period(p), log=log))
    if items is None:
        return
    def run(selected: list[dict]) -> None:
        with st.spinner("寫入請款記錄中…"):
            added = fixed.submit_rows(batch.with_taichung(selected), tool_name=batch.TOOL_NAME, log=log)
        st.success("請款記錄：" + ("、".join(f"{a} 新增 {len(r)} 筆" for a, r in added.items()) or "沒有新增（都已新增過）"))
        _queue_transfer(added.get(batch.AREA, []), create_task, log, username or batch.TOOL_NAME)

    render_request_items(PREFIX, items, log, tool_name=batch.TOOL_NAME, editable_amount=True,
                         on_run=run, run_label="寫入請款記錄並富邦轉帳：勾選的 {n} 筆合併一筆轉給勤信")


def _queue_transfer(added: list[dict], create_task: Callable, log: Callable[[str], None], created_by: str) -> None:
    """剛新增的請款記錄／台北各列，排【整合執行】富邦內部請款同一套轉帳（合併成一筆，轉完回填已付款＋下載明細）。"""
    if not added:
        st.info("台北沒有新增的列，不轉帳。")
        return
    if any(int(i["row"]) <= 0 for i in added):
        st.warning("已寫入請款記錄／台北，但讀不到新增的列號，沒有排轉帳；請到【整合執行】富邦內部請款付款。")
        return
    transfer = [{"kind": "payment", "area": batch.AREA, "row": int(i["row"]), "month": "", "payee": i["payee"],
                 "account": i.get("account", ""), "amount": str(i["amount"])} for i in added]
    merge = {(batch.AREA, t["row"]) for t in transfer}
    payment.queue_actions(transfer, create_task, created_by=created_by, log=log, merge_rows=merge)
    total = sum(int(i["amount"]) for i in added)
    st.success(f"已排入本機 Agent：台北富邦 → {batch.PAYEE}（第 {'、'.join(str(t['row']) for t in transfer)} 列合併一筆 "
               f"NT$ {total:,}）。到確認頁請核對後在電腦上按『確認』；轉完自動回填已付款＋付款日期、下載富邦明細。進度看下方。")
