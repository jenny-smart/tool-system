"""【整合執行】月初（1日所有排程）：輸入期別（上個月）→ 盤點（每個功能 × 各地區）→
沒完成的功能預設打勾（可全選）→ 執行：能在 Tools App 直接做的直接做，要 Mac 的排入本機 Agent。"""
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
DONE_COLUMN = "完成"


def first_day_after(period: str) -> date:
    year, month = int(period[:4]), int(period[4:])
    return date(year + (month == 12), 1 if month == 12 else month + 1, 1)


def _how(row: dict) -> str:
    if row["done"]:
        return row.get("note", "")
    if not row["runnable"]:
        return "已排入本機 Agent／執行中" if any(row["cells"][a][0] == chk.WAIT for a in row["todo"]) else row.get("note", "")
    return {"monthly": "這裡直接跑（只跑沒完成的地區）", "vip": "這裡直接跑（排在結算／預收之後）",
            "fixed": "直接新增到請款記錄", "invoice_update": "讀 Drive 直接更新（沒有鯨躍檔就排入鯨躍下載）",
            "pdf": "排入本機 Agent（要鯨躍驗證碼）", "agent": "排入本機 Agent（藍新每區登入一次）",
            "cash_gap": "有排藍新就跟在後面算，沒有就直接算",
            "statement": "台北／台中排入本機 Agent；其他地區由各區處理"}.get(row["action"], "")


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每月 1 日要做的所有工作，依功能列出各地區狀態：✅ 完成　❌ 沒完成／失敗　⏳ 排入本機 Agent、執行中　"
               "⚪ 查無資料（算完成）　— 這區不用做。全部地區都完成會在「完成」欄打 ✅。"
               "沒完成的功能預設打勾，按「執行」只補做沒完成的地區。"
               "（1 日 02:00～23:00 排程也會每小時檢查並提醒，固定費用、儲值金彙整沒做會自動補做。）")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    period = st.text_input("期別（YYYYMM，預設上個月；11/1 做 202610）", value=chk.previous_period(today),
                           key=f"{PREFIX}_period").strip()
    if st.button("🔍 盤點", key=f"{PREFIX}_run", type="primary"):
        if len(period) != 6 or not period.isdigit() or not 1 <= int(period[4:]) <= 12:
            st.error("期別請輸入 YYYYMM，例如 202610")
            return
        with st.spinner("讀取月排程 Log、儲值金紀錄、藍新／鯨躍紀錄、請款記錄、本機 Agent 任務中…"):
            st.session_state[f"{PREFIX}_result"] = (period, chk.collect_rows(first_day_after(period), log=log))
        reset_select_all(PREFIX)

    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result[0] != period:
        return
    rows = result[1]
    day = first_day_after(period)
    todo = [r for r in rows if not r["done"]]
    if not todo:
        st.success(f"✅ 期別 {period} 的月初工作全部完成")
        if st.session_state.get(f"{PREFIX}_cleared") != period:
            try:
                chk.clear_reminders(day, log=log)  # 全部完成：把之前的月初提醒刪掉
            except Exception as exc:
                st.caption(f"⚠️ 刪除之前的月初提醒失敗：{exc}")
            st.session_state[f"{PREFIX}_cleared"] = period
    else:
        st.warning(f"期別 {period}：{len(rows)} 個功能，✅ 完成 {len(rows) - len(todo)} 個、還有 **{len(todo)}** 個沒完成"
                   "（可補做的已預設打勾）。")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{
        RUN_COLUMN: checked(r["runnable"], override, r["runnable"]),
        "功能": r["label"],
        DONE_COLUMN: "✅ 完成" if r["done"] else "❌ 未完成",
        **{area: r["cells"][area][0] for area in chk.AREAS},
        "說明": _how(r),
    } for r in rows])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN])
    with st.expander("各地區明細（日期、筆數、錯誤訊息）"):
        st.dataframe(pd.DataFrame([{"功能": r["label"], **{a: f"{r['cells'][a][0]} {r['cells'][a][1]}".strip()
                                                          for a in chk.AREAS}} for r in rows]),
                     hide_index=True, width="stretch")
    selected = [r for r, flag in zip(rows, edited[RUN_COLUMN]) if flag and r["runnable"]]
    for row in (r for r, flag in zip(rows, edited[RUN_COLUMN]) if flag and not r["runnable"]):
        st.caption(f"「{row['label']}」{'已完成' if row['done'] else '已排入或執行中'}，不會重複執行。")
    st.caption("執行順序：月排程（已退款→預收→儲值金結算→儲值金預收，只跑沒完成的地區）→ 儲值金彙整 → 固定費用 → "
               "紙本／中獎更新 → 排入本機 Agent（富邦／元大、台中PDF、藍新、鯨躍、現金缺口）。"
               "月排程與儲值金要幾分鐘，請勿關閉頁面。")
    if st.button(f"▶️ 執行勾選的 {len(selected)} 個功能", key=f"{PREFIX}_do", disabled=not selected):
        with st.spinner("執行中…"):
            messages = chk.run_rows(selected, day, create_task, log=log, created_by=username or "月初整合執行")
        for message in messages:
            (st.error if message.startswith("❌") else st.success if message.startswith("✅") else st.info)(message)
        st.caption("本機 Agent 的項目需要一點時間，稍後再按一次「盤點」確認。")
        st.session_state.pop(f"{PREFIX}_result", None)
