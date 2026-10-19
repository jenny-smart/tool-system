"""【清潔異動】已備妥整合執行：盤點 → 勾選 → 一次排入本機 Agent → 回寫系統。"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from tools.finance_management import change_order_batch as batch

STATE_KEY = "change_order_batch_items"
RUN_COLUMN = "執行"


def _rows(items: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            RUN_COLUMN: bool(item["actions"]),
            "地區": item["region"],
            "列號": item["row"],
            "狀態": item["status"],
            "細項": item["detail"],
            "訂單編號": item["order_no"],
            "客人": item["customer"],
            "付款方式": item["payway"],
            "金額": item["amount"],
            "進度": "、".join(item["progress"]),
            "本次動作": "、".join(batch.ACTIONS[a][0] for a in item["actions"]),
            "未備妥（缺資料）": "、".join(item["missing"]),
            "需人工": "；".join(item["manual"]),
            "後台備註": item["note"],
        }
        for item in items
    ])


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption(
        "只處理登記日期 2026/10/16 以後的列。盤點時會補 T（匯款金額）、Z（未稅金額）公式；"
        "開立發票、二聯改三聯需逐筆確認，請到【鯨躍發票】對應功能處理。"
    )
    regions = st.multiselect("地區", batch.REGIONS, default=batch.REGIONS, key="change_order_batch_regions")
    if st.button("🔍 盤點", key="change_order_batch_scan", type="primary"):
        with st.spinner("讀取各區清潔異動中…"):
            st.session_state[STATE_KEY] = batch.scan(regions, log=log)

    items = st.session_state.get(STATE_KEY)
    if items is None:
        return
    if not items:
        st.success("沒有需要處理的列 🎉")
        return

    runnable = [item for item in items if item["actions"]]
    missing = [item for item in items if item["missing"]]
    manual = [item for item in items if not item["actions"] and not item["missing"]]
    st.markdown(f"共 **{len(items)}** 列：可自動執行 **{len(runnable)}** 列、"
                f"未備妥（缺資料）**{len(missing)}** 列、需人工 **{len(manual)}** 列。")
    edited = st.data_editor(
        _rows(items),
        hide_index=True,
        width="stretch",
        disabled=[c for c in _rows(items[:1]).columns if c != RUN_COLUMN],
        key="change_order_batch_editor",
    )
    selected = [item for item, flag in zip(items, edited[RUN_COLUMN]) if flag and item["actions"]]
    counts = {}
    for item in selected:
        for action in item["actions"]:
            counts[action] = counts.get(action, 0) + 1
    if counts:
        st.write("本次會執行：" + "、".join(f"{batch.ACTIONS[a][0]} {n} 筆" for a, n in counts.items()))
    do_sync = st.checkbox("執行完後回寫系統（B 欄已完成、AE 尚未更新系統的列）", value=True,
                          key="change_order_batch_sync")
    st.caption("執行順序：鯨躍折讓單 → 富邦 ATM 退款（需手機核准）→ 藍新信用卡退款 → 檸檬後台儲值金 → 回寫系統。"
               "回寫完成會在 Jenny 行事曆留一筆紀錄。")

    sync_only = [item for item in selected if "sync" in item["actions"]]
    if st.button(f"📝 只回寫系統（勾選中可回寫的 {len(sync_only)} 筆，不執行其他動作）",
                 key="change_order_batch_sync_only", disabled=not sync_only):
        rows = batch.selected_rows_by_region(sync_only)
        with st.spinner("回寫系統中…"):
            results = batch.sync(list(rows), log=log, rows=rows)
        done = sum(len(r["done"]) for r in results.values())
        failed = sum(len(r["failed"]) for r in results.values())
        (st.success if not failed else st.warning)(f"回寫完成 {done} 筆，失敗 {failed} 筆")
        st.session_state.pop(STATE_KEY, None)

    if st.button("▶️ 執行勾選的動作", key="change_order_batch_run", disabled=not selected):
        agent_items = [item for item in selected if set(item["actions"]) - {"sync"}]
        if agent_items:
            batch.queue_actions(selected, do_sync, create_task, created_by=username or "清潔異動整合執行", log=log)
            st.success("已排入本機 Agent，請到下方查看執行進度；完成後可再按一次「盤點」確認。")
        elif do_sync:
            with st.spinner("回寫系統中…"):
                results = batch.sync(list(batch.selected_rows_by_region(selected)), log=log,
                                     rows=batch.selected_rows_by_region(selected))
            done = sum(len(r["done"]) for r in results.values())
            failed = sum(len(r["failed"]) for r in results.values())
            (st.success if not failed else st.warning)(f"回寫完成 {done} 筆，失敗 {failed} 筆")
        st.session_state.pop(STATE_KEY, None)
