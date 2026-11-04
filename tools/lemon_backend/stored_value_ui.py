"""【檸檬後台】異動儲值金（雲端執行）：盤點 → 勾選（可全選）→ 執行。

盤點列出清潔異動表待扣／待返儲值金、VIP待退券（未歸零）的列；VIP待退券會登入後台讀餘額，
把歸零要扣的每一筆金額都算出來給人確認。執行在 Tools App 用無頭瀏覽器完成，不需要 Mac 開機。
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls
from tools.lemon_backend.stored_value_sheet import SUPPORTED_AREAS

PREFIX = "stored_value_cloud"
RUN_COLUMN = "執行"


def _steps_text(item: dict) -> str:
    return "＋".join(f"{suffix} NT$ {int(amount):,}" for suffix, amount in item["steps"]) or "已是 0，只回寫"


def _balance_text(item: dict) -> str:
    balances = item.get("balances")
    return f"儲值金 {balances[0]:,}／購物金 {balances[1]:,}" if balances else ""


def render(log: Callable[[str], None], username: str = "") -> None:
    from tools.lemon_backend import stored_value_adjustment as adjustment

    st.caption("盤點清潔異動表的待扣／待返儲值金、VIP待退券（未歸零）；VIP待退券會先讀後台餘額，"
               "列出歸零要扣的每一筆金額。確認後按「執行」在雲端送出，不需要 Mac。")
    area = st.selectbox("地區", SUPPORTED_AREAS, key=f"{PREFIX}_area")
    state_key = f"{PREFIX}_items"
    if st.button("🔍 盤點", key=f"{PREFIX}_scan", type="primary"):
        try:
            with st.spinner("讀取清潔異動表、後台餘額中…"):
                st.session_state[state_key] = (area, adjustment.plan_adjustments(area, log=log))
            reset_select_all(PREFIX)
        except Exception as exc:
            st.error(f"盤點失敗：{exc}")
    scanned = st.session_state.get(state_key)
    if not scanned:
        return
    if scanned[0] != area:
        st.info("地區已變更，請重新按「盤點」。")
        return
    items = scanned[1]
    if not items:
        st.success(f"{area}目前沒有待異動的儲值金。")
        return
    st.markdown(f"共 **{len(items)}** 筆（已預設打勾）。執行時若 VIP待退券的餘額跟盤點時不同，該列會略過。")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{
        RUN_COLUMN: checked(True, override),
        "列數": item["sheet_row"], "狀態": item["status"], "訂單編號": item["order_no"],
        "異動": item["action"], "要送出的金額": _steps_text(item), "目前餘額": _balance_text(item),
        "備註": item["note"],
    } for item in items])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN],
                            column_config={RUN_COLUMN: st.column_config.CheckboxColumn(RUN_COLUMN)})
    selected = [item for item, flag in zip(items, edited[RUN_COLUMN]) if flag]
    st.caption(f"已勾選 {len(selected)} 筆")
    if st.button("▶️ 執行勾選的異動", key=f"{PREFIX}_run", disabled=not selected):
        with st.spinner("後台送出中…"):
            try:
                result = adjustment.execute_plan(area, selected, log=log)
            except Exception as exc:
                st.error(f"執行失敗：{exc}")
                return
        st.session_state.pop(state_key, None)
        message = f"完成 {result['done']} 筆、略過 {result['skipped']} 筆、失敗 {result['failed']} 筆"
        (st.success if not result["failed"] else st.warning)(message)
        try:
            from tools.finance_management.execution_log import log_execution

            log_execution("【檸檬後台】異動儲值金", area, "完成" if not result["failed"] else "部分失敗",
                          f"{message}（{username or 'Tools App'}）")
        except Exception:
            pass
