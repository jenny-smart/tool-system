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
        "待收款開立發票同【鯨躍發票】開立發票（沿用訂單發票設定，需人工按下一步及儲存）；"
        "二聯改三聯請到【鯨躍發票】對應功能處理。"
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

    sync_only = st.checkbox(
        "📝 僅回寫系統（只列出 B 欄已完成或資料已處理好、尚未回寫的列；不執行折讓單、退款、儲值金等動作）",
        value=False, key="change_order_batch_sync_only_mode")
    if sync_only:
        items = [item for item in items if item["actions"] == ["sync"]]
        if not items:
            st.info("沒有需要回寫系統的列。")
            return

    runnable = [item for item in items if item["actions"]]
    missing = [item for item in items if item["missing"] and not item["actions"]]
    manual = [item for item in items if not item["actions"] and not item["missing"]]
    if sync_only:
        st.markdown(f"共 **{len(items)}** 列可回寫系統。")
    else:
        st.markdown(f"共 **{len(items)}** 列：可自動執行 **{len(runnable)}** 列、"
                    f"未備妥（缺資料）**{len(missing)}** 列、需人工 **{len(manual)}** 列。")
    edited = st.data_editor(
        _rows(items),
        hide_index=True,
        width="stretch",
        disabled=[c for c in _rows(items[:1]).columns if c != RUN_COLUMN],
        key=f"change_order_batch_editor_{'sync' if sync_only else 'all'}",
    )
    selected = [item for item, flag in zip(items, edited[RUN_COLUMN]) if flag and item["actions"]]
    for item in (item for item, flag in zip(items, edited[RUN_COLUMN]) if flag and not item["actions"]):
        st.warning(f"{item['region']} 第 {item['row']} 列（{item['status']}）沒有可自動執行的動作，勾選不會執行："
                   f"{blocked_reason(item)}")

    if sync_only:
        if st.button(f"▶️ 回寫勾選的 {len(selected)} 筆", key="change_order_batch_run", disabled=not selected):
            _sync_now(selected, log)
        return

    counts = {}
    for item in selected:
        for action in item["actions"]:
            counts[action] = counts.get(action, 0) + 1
    if counts:
        st.write("本次會執行：" + "、".join(f"{batch.ACTIONS[a][0]} {n} 筆" for a, n in counts.items()))
    do_sync = st.checkbox("執行完後回寫系統（B 欄已完成、AE 尚未更新系統的列）", value=True,
                          key="change_order_batch_sync")
    st.caption("執行順序：鯨躍開立發票（每筆需人工按下一步及儲存）→ 鯨躍折讓單 → 富邦 ATM 退款（需手機核准）→ 藍新信用卡退款 → 檸檬後台儲值金 → 回寫系統。"
               "回寫完成會在 Jenny 行事曆留一筆紀錄。")

    if st.button("▶️ 執行勾選的動作", key="change_order_batch_run", disabled=not selected):
        agent_items = [item for item in selected if set(item["actions"]) - {"sync"}]
        if agent_items:
            batch.queue_actions(selected, do_sync, create_task, created_by=username or "清潔異動整合執行", log=log,
                                queue_invoices=_queue_invoices)
            st.success("已排入本機 Agent，請到下方查看執行進度；完成後可再按一次「盤點」確認。")
            st.session_state.pop(STATE_KEY, None)
        elif do_sync:
            _sync_now(selected, log)


def blocked_reason(item: dict) -> str:
    """勾選了但沒有可執行動作的列：說明原因（缺資料、需人工、或進度上已在等待）。"""
    reasons = item["missing"] + item["manual"] + item["progress"]
    return "；".join(reasons) or "目前沒有需要執行的動作"


def _queue_invoices(region: str, rows: list[int]) -> dict:
    """同【鯨躍發票】開立發票：依訂單發票設定組資料、排入鯨躍填表任務。"""
    from tools.invoice_center import ui as invoice_ui

    return invoice_ui.queue_pending_invoices(region, rows)


def _sync_now(selected: list[dict], log: Callable[[str], None]) -> None:
    """只需回寫的列直接在雲端回寫（不經本機 Agent）。"""
    rows = batch.selected_rows_by_region(selected)
    with st.spinner("回寫系統中…"):
        results = batch.sync(list(rows), log=log, rows=rows)
    done = sum(len(r["done"]) for r in results.values())
    failed = sum(len(r["failed"]) for r in results.values())
    (st.success if not failed else st.warning)(f"回寫完成 {done} 筆，失敗 {failed} 筆")
    st.session_state.pop(STATE_KEY, None)
