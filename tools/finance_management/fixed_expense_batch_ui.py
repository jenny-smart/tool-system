"""【整合執行】固定費用（辦公室租金）：盤點 → 勾選（可全選）→ 新增到請款記錄。

render_request_items() 也給【整合執行】會計事務所費用確認共用（可編輯金額）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management import fixed_expense_batch as batch
from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "fixed_expense_batch"
RUN_COLUMN = "執行"
AMOUNT_COLUMN = "金額"


def scan_button(prefix: str, period: str, scan: Callable[[str], list[dict]]) -> Optional[list[dict]]:
    """「盤點」按鈕＋暫存結果；期別變了就要重新盤點。"""
    state_key = f"{prefix}_items"
    if st.button("🔍 盤點", key=f"{prefix}_scan", type="primary"):
        try:
            with st.spinner("讀取請款記錄中…"):
                st.session_state[state_key] = (period, scan(period))
            reset_select_all(prefix)
        except ValueError as exc:
            st.error(str(exc))
    scanned = st.session_state.get(state_key)
    if not scanned:
        return None
    if scanned[0] != period:
        st.info("期別已變更，請重新按「盤點」。")
        return None
    return scanned[1]


def render_request_items(prefix: str, items: list[dict], log: Callable[[str], None], *,
                         tool_name: str, editable_amount: bool = False) -> None:
    """盤點結果表格：可新增的預設打勾；editable_amount 時金額可改（空白的要填才會新增）。"""
    runnable = [i for i in items if i["default"]]
    st.markdown(f"共 **{len(items)}** 筆：目前可新增 **{len(runnable)}** 筆（已預設打勾）。")
    override, editor_key = select_all_controls(prefix)
    table = pd.DataFrame([{
        RUN_COLUMN: checked(i["default"], override, i["selectable"]),
        "地區": i["area"], "費用說明": i["memo"], "請款類別": i["category"],
        AMOUNT_COLUMN: None if i["amount"] in ("", None) else int(i["amount"]),
        "收款對象": i["payee"], "狀態": i["status"], "備註": i.get("note", ""),
    } for i in items])
    editable = {RUN_COLUMN} | ({AMOUNT_COLUMN} if editable_amount else set())
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c not in editable],
                            column_config={AMOUNT_COLUMN: st.column_config.NumberColumn(AMOUNT_COLUMN, min_value=0, step=1)})
    selected, blank = [], []
    for item, flag, amount in zip(items, edited[RUN_COLUMN], edited[AMOUNT_COLUMN]):
        if not flag:
            continue
        if not item["selectable"]:
            st.warning(f"{item['memo']}：{item['status']}，不會新增。")
            continue
        if pd.isna(amount) or int(amount) <= 0:
            blank.append(item["memo"])
            continue
        selected.append(dict(item, amount=int(amount)))
    if blank:
        st.warning("請先填金額：" + "、".join(blank))
    if selected:
        st.write(f"本次新增：{len(selected)} 筆，合計 NT$ {sum(i['amount'] for i in selected):,}")
    if st.button(f"▶️ 新增勾選的 {len(selected)} 筆到請款記錄", key=f"{prefix}_run", disabled=not selected or bool(blank)):
        with st.spinner("新增請款記錄中…"):
            added = batch.submit(selected, tool_name=tool_name, log=log)
        st.success("完成：" + "、".join(f"{a} 新增 {n} 筆" for a, n in added.items())
                   + "。付款請到【整合執行】富邦內部請款。")
        st.session_state.pop(f"{prefix}_items", None)


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("台北每月 1 日：當月辦公室租金 77,343、管理費 9,392；台中每單月 1 日：當月＋下月租金管理費各 18,000。"
               "每月 1 日 00:30 排程會自動新增；這裡可盤點、補新增。已新增過的不會重複。")
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    items = scan_button(PREFIX, period, lambda p: batch.scan(_checked_period(p), log=log))
    if items is None:
        return
    if not items:
        st.success("沒有要新增的固定費用 🎉")
        return
    render_request_items(PREFIX, items, log, tool_name=batch.TOOL_NAME)


def _checked_period(period: str) -> str:
    if len(period) != 6 or not period.isdigit() or not 1 <= int(period[4:]) <= 12:
        raise ValueError("期別請輸入 YYYYMM，例如 202611")
    return period
