"""【整合執行】月初藍新／鯨躍／現金缺口：盤點 → 勾選（可全選）→ 排入本機 Agent。"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management import month_start_batch as batch
from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "month_start_batch"
STATE_KEY = f"{PREFIX}_items"
RUN_COLUMN = "執行"


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每月 1 日要做的事：藍新收退款＋發票金額、鯨躍發票下載（本機 Agent，藍新每區需人工輸入驗證碼）、"
               "全區現金缺口。每月 1 日 05:00 排程會自動排入（藍新約 08:00 寄手續費發票，太早查不到會算失敗、可再補做）；這裡可盤點目前狀態、補排沒做到的步驟。")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    period = st.text_input("期別（YYYYMM，預設上個月）", value=batch.previous_period(today),
                           key=f"{PREFIX}_period").strip()
    if st.button("🔍 盤點", key=f"{PREFIX}_scan", type="primary"):
        try:
            with st.spinner("讀取本機 Agent 任務與試算表中…"):
                st.session_state[STATE_KEY] = (period, batch.scan(period, log=log))
            reset_select_all(PREFIX)
        except ValueError as exc:
            st.error(str(exc))

    scanned = st.session_state.get(STATE_KEY)
    if not scanned:
        return
    scanned_period, items = scanned
    if scanned_period != period:
        st.info("期別已變更，請重新按「盤點」。")
        return

    runnable = [i for i in items if i["default"]]
    st.markdown(f"期別 **{scanned_period}**：目前可做 **{len(runnable)}** 項（已預設打勾）。")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{RUN_COLUMN: checked(i["default"], override, i["selectable"]), "步驟": i["label"],
                           "目前狀態": i["status"], "說明": i["note"]} for i in items])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN])
    selected = [i for i, flag in zip(items, edited[RUN_COLUMN]) if flag and i["selectable"]]
    for item in (i for i, flag in zip(items, edited[RUN_COLUMN]) if flag and not i["selectable"]):
        st.warning(f"{item['label']}：{item['status']}，不會重複排入。")
    if any(i["step"] == "cash_gap" for i in selected):
        st.caption("現金缺口一律照算；藍新發票金額沒更新時，會用藍新發票總表目前的數字（之後補做藍新再算一次就好）。")
    st.caption("依序執行：藍新收退款＋發票金額（每區只登入一次、只輸入一次驗證碼）→ 鯨躍發票 → 現金缺口；"
               "每一步失敗都不影響後面的步驟。"
               "跑完會在 Jenny 行事曆留紀錄。")
    if st.button(f"▶️ 執行勾選的 {len(selected)} 項", key=f"{PREFIX}_run", disabled=not selected):
        batch.queue_steps(scanned_period, [i["step"] for i in selected], create_task,
                          created_by=username or "月初整合執行", log=log)
        st.success("已排入本機 Agent，請到下方查看執行進度。")
        st.session_state.pop(STATE_KEY, None)
