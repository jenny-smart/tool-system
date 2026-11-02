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
            "pdf": "排入本機 Agent（要鯨躍驗證碼）",
            "agent": "排入本機 Agent（藍新收退款／發票／帳務勾哪個做哪個，每區登入一次）",
            "cash_gap": "同時勾「藍新發票」：等本機 Agent 查完藍新發票金額再算；沒勾：按執行就直接重算",
            "revenue_batch": "這裡直接補跑未完成地區；⑥–⑩與搬入營收總表＋財報分開執行",
            "statement": "台北／台中排入本機 Agent；其他地區由各區處理"}.get(row["action"], "")


def _saved_ignored(names: list[str], options: list[tuple[str, str, str]]) -> list[str]:
    """已存的不提醒設定（含已完成的項目，multiselect 預設值要完整顯示）。"""
    try:
        values = chk._read_master(chk.IGNORE_SHEET, "A:D")
    except Exception:
        return []
    saved = chk.read_ignored(values)
    return [n for n, (k, _l, a) in zip(names, options) if (k, a) in saved]


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每月 1 日要做的所有工作，依功能列出各地區狀態：✅ 完成　❌ 沒完成／失敗　⏳ 排入本機 Agent、執行中　"
               "— 這區不用做（藍新查無發票算 ❌；確定沒有發票就到「不提醒設定」勾掉）。全部地區都完成會在「完成」欄打 ✅。"
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
        RUN_COLUMN: checked(r["runnable"] and bool(r.get("remind_todo", r["todo"])), override, r["runnable"]),
        "功能": r["label"],
        DONE_COLUMN: "✅ 完成" if r["done"] else "❌ 未完成（不提醒）" if not r.get("remind_todo", r["todo"]) else "❌ 未完成",
        **{area: r["cells"][area][0] for area in chk.AREAS},
        "說明": _how(r),
    } for r in rows])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN])
    with st.expander("🔕 不提醒設定（自己沒辦法完成的項目：盤點照樣顯示 ❌，但行事曆和 1 日每小時檢查不再提醒）"):
        options = chk.ignore_options(rows)
        names = [f"{label}／{area}" for _k, label, area in options]
        current = [n for n, (k, _l, a) in zip(names, options) if any(k == r["key"] and a in r.get("ignored", [])
                                                                     for r in rows)]
        saved = st.session_state.get(f"{PREFIX}_ignored_saved")
        picked = st.multiselect("不提醒的功能／地區（每個月都適用）", names,
                                default=saved if saved is not None else _saved_ignored(names, options),
                                key=f"{PREFIX}_ignored")
        if st.button("💾 儲存不提醒設定", key=f"{PREFIX}_ignored_save"):
            try:
                count = chk.save_ignored([options[names.index(n)] for n in picked])
                st.session_state[f"{PREFIX}_ignored_saved"] = picked
                st.success(f"已儲存 {count} 項不提醒，請再按一次「盤點」")
                st.session_state.pop(f"{PREFIX}_result", None)
            except Exception as exc:
                st.error(f"儲存失敗：{exc}")
        if current:
            st.caption("這次盤點沒完成但不提醒：" + "、".join(current))
    with st.expander("各地區明細（日期、筆數、錯誤訊息）"):
        st.dataframe(pd.DataFrame([{"功能": r["label"], **{a: f"{r['cells'][a][0]} {r['cells'][a][1]}".strip()
                                                          for a in chk.AREAS}} for r in rows]),
                     hide_index=True, width="stretch")
    selected = [r for r, flag in zip(rows, edited[RUN_COLUMN]) if flag and r["runnable"]]
    for row in (r for r, flag in zip(rows, edited[RUN_COLUMN]) if flag and not r["runnable"]):
        st.caption(f"「{row['label']}」{'已完成' if row['done'] else '已排入或執行中'}，不會重複執行。")
    st.caption("執行順序：月排程 → 儲值金彙整 → 財報營收⑥–⑩ → 搬入營收總表＋財報 → 固定費用 → "
               "紙本／中獎更新 → 排入本機 Agent（富邦／元大、台中PDF、藍新、鯨躍、現金缺口）。"
               "月排程與儲值金要幾分鐘，請勿關閉頁面。")
    if st.button(f"▶️ 執行勾選的 {len(selected)} 個功能", key=f"{PREFIX}_do", disabled=not selected):
        with st.spinner("執行中…"):
            messages = chk.run_rows(selected, day, create_task, log=log, created_by=username or "月初整合執行")
        for message in messages:
            (st.error if message.startswith("❌") else st.success if message.startswith("✅") else st.info)(message)
        st.caption("本機 Agent 的項目需要一點時間，稍後再按一次「盤點」確認。")
        st.session_state.pop(f"{PREFIX}_result", None)
