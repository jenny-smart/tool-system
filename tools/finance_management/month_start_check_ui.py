"""【整合執行】月初檢查（1日所有排程）：選月份 → 按「檢查」→ 列出 1 日所有排程／工作的狀態與補做方式。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management import month_start_check as chk

PREFIX = "month_start_check"


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("檢查某個月 1 日該完成的工作：預收、儲值金結算／預收、儲值金完整流程、固定費用、"
               "藍新收退款、藍新發票金額、鯨躍發票、現金缺口。沒完成的會列出補做方式。"
               "（1 日 02:00～23:00 排程也會每小時自動檢查並建行事曆提醒。）")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    month = st.text_input("月份（YYYYMM，檢查這個月的 1 日）", value=today.strftime("%Y%m"), key=f"{PREFIX}_month").strip()
    if st.button("🔍 檢查", key=f"{PREFIX}_run", type="primary"):
        if len(month) != 6 or not month.isdigit() or not 1 <= int(month[4:]) <= 12:
            st.error("月份請輸入 YYYYMM，例如 202611")
            return
        with st.spinner("讀取排程紀錄、請款記錄、本機 Agent 任務中…"):
            st.session_state[f"{PREFIX}_result"] = (month, chk.collect_all(date(int(month[:4]), int(month[4:]), 1), log=log))

    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result[0] != month:
        return
    entries = result[1]
    todo = [e for e in entries if not e["ok"]]
    if todo:
        st.warning(f"{month[:4]}/{month[4:]}/01：{len(entries)} 項中還有 **{len(todo)}** 項沒完成。")
    else:
        st.success(f"{month[:4]}/{month[4:]}/01 的工作全部完成 🎉")
    st.dataframe(pd.DataFrame([{"": "✅" if e["ok"] else "❌", "項目": e["label"], "狀態": e["status"],
                                "處理方式": "" if e["ok"] else e["fix"]} for e in entries]),
                 hide_index=True, width="stretch")
    if todo:
        st.markdown("**補做方式**")
        for fix in dict.fromkeys(e["fix"] for e in todo):
            st.markdown(f"- {fix}")
