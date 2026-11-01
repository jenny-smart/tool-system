"""【整合執行】月初檢查（1日所有排程）：選月份 → 按「檢查」→ 列出 1 日所有工作的狀態 →
勾選沒完成的（預設打勾、可全選）→ 直接補做。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management import month_start_check as chk
from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "month_start_check"
RUN_COLUMN = "執行"


def _how(entry: dict) -> str:
    if entry["ok"]:
        return ""
    if entry.get("kind") == "statement" and not entry.get("remind", True):
        return "由各區財務更新（排程不提醒）"
    if not entry["runnable"]:
        return "排隊中／執行中，等本機 Agent 跑完" if entry.get("kind") == "month" else entry["fix"]
    return {"cloud": "觸發 GitHub 排程重跑", "fixed": "直接新增到請款記錄",
            "statement": "排入本機 Agent 下載明細（財報最後一列日期～今天）",
            "month": "排入本機 Agent（現金缺口單獨勾就直接算）"}[entry["kind"]]


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("檢查某個月 1 日該完成的工作：預收、儲值金結算／預收、儲值金完整流程、固定費用、"
               "藍新收退款、藍新發票金額、鯨躍發票、現金缺口、各區富邦／元大明細（桃園／新竹／高雄只顯示）。"
               "沒完成的預設打勾，按「執行」直接補做。"
               "（1 日 02:00～23:00 排程也會每小時自動檢查並建行事曆提醒。）")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    month = st.text_input("月份（YYYYMM，檢查這個月的 1 日）", value=today.strftime("%Y%m"), key=f"{PREFIX}_month").strip()
    if st.button("🔍 檢查", key=f"{PREFIX}_run", type="primary"):
        if len(month) != 6 or not month.isdigit() or not 1 <= int(month[4:]) <= 12:
            st.error("月份請輸入 YYYYMM，例如 202611")
            return
        with st.spinner("讀取排程紀錄、請款記錄、本機 Agent 任務中…"):
            st.session_state[f"{PREFIX}_result"] = (month, chk.collect_all(date(int(month[:4]), int(month[4:]), 1), log=log))
        reset_select_all(PREFIX)

    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result[0] != month:
        return
    entries = result[1]
    day = date(int(month[:4]), int(month[4:]), 1)
    todo = [e for e in entries if not e["ok"]]
    others = [e for e in todo if not e.get("remind", True)]
    todo = [e for e in todo if e.get("remind", True)]
    if others:
        st.caption(f"桃園／新竹／高雄的富邦元大明細有 {len(others)} 項未更新到月底（由各區處理，排程不提醒，現金缺口會用目前的餘額）。")
    if not todo:
        st.success(f"{day:%Y/%m/%d} 的工作全部完成 🎉")
        if st.session_state.get(f"{PREFIX}_cleared") != month:
            try:
                chk.clear_reminders(day, log=log)  # 全部完成：把之前的月初提醒刪掉
            except Exception as exc:
                st.caption(f"⚠️ 刪除之前的月初提醒失敗：{exc}")
            st.session_state[f"{PREFIX}_cleared"] = month
    else:
        st.warning(f"{day:%Y/%m/%d}：{len(entries)} 項中還有 **{len(todo)}** 項沒完成（可補做的已預設打勾）。")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{RUN_COLUMN: checked(e["runnable"], override, e["runnable"]),
                           "": "✅" if e["ok"] else "❌", "項目": e["label"], "狀態": e["status"],
                           "補做方式": _how(e)} for e in entries])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN])
    selected = [e for e, flag in zip(entries, edited[RUN_COLUMN]) if flag and e["runnable"]]
    for entry in (e for e, flag in zip(entries, edited[RUN_COLUMN]) if flag and not e["runnable"]):
        st.caption(f"「{entry['label']}」{'已完成' if entry['ok'] else '排隊中或執行中'}，不會重複執行。")
    _selected, notes = chk.plan_run(selected)
    for note in notes:
        st.info(note)
    if st.button(f"▶️ 執行勾選的 {len(selected)} 項", key=f"{PREFIX}_do", disabled=not selected):
        with st.spinner("補做中…"):
            messages = chk.run_selected(selected, day, create_task, log=log, created_by=username or "月初檢查")
        for message in messages:
            (st.error if message.startswith("❌") else st.success if message.startswith("✅") else st.info)(message)
        st.caption("雲端排程與本機 Agent 需要一點時間，稍後再按一次「檢查」確認。")
        st.session_state.pop(f"{PREFIX}_result", None)
