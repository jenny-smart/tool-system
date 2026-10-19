"""【富邦銀行】內部請款整合執行：盤點台北／台中待付款 → 勾選 → 排入本機 Agent → 留紀錄。"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from tools.finance_management import internal_payment_batch as batch

STATE_KEY = "internal_payment_batch_items"
RUN_COLUMN = "執行"


def _rows(items: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            RUN_COLUMN: True,
            "地區": item["area"],
            "類型": batch.KINDS[item["kind"]][0],
            "列號": "、".join(map(str, item["rows"])),
            "採購月份": item.get("month", ""),
            "收款對象": item.get("payee", ""),
            "金額": item.get("amount", ""),
            "說明": item.get("memo", ""),
        }
        for item in items
    ])


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("盤點台北、台中的請款記錄、工具包押金退款、清潔用品採購待付款；勾選後交給本機 Agent 用富邦網銀轉帳"
               "（每筆仍需手機核准）。全部跑完會在 Jenny 行事曆留紀錄。")
    areas = st.multiselect("地區", batch.AREAS, default=batch.AREAS, key="internal_payment_batch_areas")
    if st.button("🔍 盤點", key="internal_payment_batch_scan", type="primary"):
        with st.spinner("讀取待付款資料中…"):
            st.session_state[STATE_KEY] = batch.scan(areas, log=log)

    items = st.session_state.get(STATE_KEY)
    if items is None:
        return
    if not items:
        st.success("沒有待付款資料 🎉")
        return

    st.markdown(f"共 **{len(items)}** 筆待付款。")
    edited = st.data_editor(_rows(items), hide_index=True, width="stretch",
                            disabled=[c for c in _rows(items[:1]).columns if c != RUN_COLUMN],
                            key="internal_payment_batch_editor")
    selected = [item for item, flag in zip(items, edited[RUN_COLUMN]) if flag]
    st.caption("執行順序：請款記錄 → 工具包押金退款 → 清潔用品採購 → 留紀錄。")
    if st.button(f"▶️ 執行勾選的 {len(selected)} 筆", key="internal_payment_batch_run", disabled=not selected):
        batch.queue_actions(selected, create_task, created_by=username or "內部請款整合執行", log=log)
        st.success("已排入本機 Agent，請到下方查看執行進度。")
        st.session_state.pop(STATE_KEY, None)
