"""【整合執行】月度盤點：選資料月份（預設上個月）→ 盤點那批資料所有該做的事 → 沒完成的預設打勾（可全選）→ 執行。"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management import monthly_checklist as mc
from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "monthly_checklist"
RUN_COLUMN = "執行"


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("資料月份（預設上個月）的所有該做的事，多半在下個月做：月初工作、工具包押金、年度review、台北查信、"
               "各區固定費用匯款、現金缺口各項數字、會計事務所費用（單月）、鯨躍字軌（下一期）。"
               "✅ 完成　❌ 未完成（已過應完成日）　⏳ 未到期　🕒 已排入本機 Agent／執行中　✋ 要到功能頁處理　— 本月不用。"
               "結果會寫進主控表「月度盤點」分頁（保留每月紀錄）；在分頁自己加的列（項目代號空白）是手動項目。")
    this_month = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m")
    data = st.text_input("資料月份（YYYYMM，預設上個月）", value=mc.previous_month(this_month),
                         key=f"{PREFIX}_month").strip()
    valid = len(data) == 6 and data.isdigit() and 1 <= int(data[4:]) <= 12
    month = mc.next_month(data) if valid else ""  # 做事的月份
    if st.button("🔍 盤點", key=f"{PREFIX}_scan", type="primary"):
        if not valid:
            st.error("資料月份請輸入 YYYYMM，例如 202610")
            return
        with st.spinner("盤點中（月初工作、押金、review、請款記錄、行銷費用總表、現金缺口、字軌 Log）…"):
            items = mc.collect(month, log=log)
            try:
                mc.save(month, items, log=log)
            except Exception as exc:
                st.caption(f"⚠️ 寫入「月度盤點」分頁失敗：{exc}")
        st.session_state[f"{PREFIX}_result"] = (month, items)
        reset_select_all(PREFIX)

    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result[0] != month:
        return
    items = result[1]
    todo = [i for i in items if i["status"] in (mc.TODO, mc.MANUAL)]
    if todo:
        st.warning(f"{data[:4]}/{data[4:]} 資料：{len(items)} 項，還有 **{len(todo)}** 項過了應完成日沒做完（可直接執行的已預設打勾）。")
    else:
        st.success(f"✅ {data[:4]}/{data[4:]} 資料：目前沒有過期未完成的項目")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{
        RUN_COLUMN: checked(i["runnable"] and i["status"] == mc.TODO, override, i["runnable"]),
        "項目": i["label"], "應完成日": i["due"].strftime("%m/%d"), "狀態": i["status"],
        "檢查結果": i["detail"] + (f"（到 {i['page']}）" if i.get("page") and i["status"] != mc.DONE else ""),
    } for i in items])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN_COLUMN])
    try:
        manual = mc.manual_rows(month)
    except Exception:
        manual = []
    if manual:
        with st.expander(f"手動項目（{len(manual)}）— 在主控表「月度盤點」分頁更新狀態"):
            st.dataframe(pd.DataFrame([{"項目": m["label"], "應完成日": m["due"], "狀態": m["status"],
                                        "備註": m["detail"]} for m in manual]), hide_index=True, width="stretch")
    selected = [i for i, flag in zip(items, edited[RUN_COLUMN]) if flag and i["runnable"]]
    st.caption("執行時：月初工作照【整合執行】月初的順序；押金、review、查信、現金缺口在這裡直接做；"
               "固定費用匯款、鯨躍字軌排入本機 Agent（要手機核准／財政部驗證碼）。要幾分鐘，請勿關閉頁面。")
    if st.button(f"▶️ 執行勾選的 {len(selected)} 項", key=f"{PREFIX}_run", disabled=not selected):
        with st.spinner("執行中…"):
            messages = mc.run_items(month, selected, create_task, log=log, created_by=username or "月度盤點")
        for message in messages:
            (st.error if message.startswith("❌") else st.success if message.startswith("✅") else st.info)(message)
        st.caption("本機 Agent 的項目需要一點時間，完成後再按一次「盤點」確認。")
        st.session_state.pop(f"{PREFIX}_result", None)
