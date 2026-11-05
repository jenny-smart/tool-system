"""【整合執行】各區固定費用匯款（每月10日）：盤點 → 勾選 → 執行。

- 台中／桃園／新竹：行銷費用總表第72列（第74列空白＝還沒匯）
- 高雄 40000：每月固定匯給台北（台北內勤薪資減項；高雄財報記內勤薪資加項）
- 學院 47911：學院自己轉帳給台北，這裡只寫台北入帳分錄（內勤薪資／勞保費／健保費／退休金四列）
- 台中（單月）：台中固定費用請款——當月＋隔月租金、前 2 個月電費新增到請款記錄（電費要輸入金額）
匯款一律排入本機 Agent，每區一筆，由你在富邦核對後手機核准；完成後同步明細並寫入台北／分店財報分錄。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "fixed_remit_batch"
RUN = "執行"


def _scan(period: str) -> dict:
    from tools.finance_management.fixed_expense_transfer import remit_status
    from tools.finance_management.taichung_fixed_expense import taichung_status

    rows = remit_status(period)
    try:
        taichung = taichung_status(period)
    except Exception as exc:
        taichung = {"applicable": True, "missing": [f"讀取失敗：{exc}"], "rent_missing": [], "electricity_missing": []}
    return {"period": period, "remit": rows, "taichung": taichung}


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    st.caption("每月 10 日：台中／桃園／新竹依行銷費用總表、高雄 40000 固定匯給台北；學院 47911 自己轉帳，確認收到後勾選寫入台北分錄。"
               "單月另外新增台中固定費用請款（租金、電費）。匯款排入本機 Agent，每區由你在富邦核對後手機核准。"
               "排程（排程設定 fixed_expense_remit）每月 10 日會自動盤點並排入。")
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    if st.button("🔍 盤點", key=f"{PREFIX}_scan", type="primary"):
        if len(period) != 6 or not period.isdigit():
            st.error("期別請輸入 YYYYMM，例如 202611")
            return
        with st.spinner("讀取行銷費用總表、台北財報、台中請款記錄…"):
            try:
                st.session_state[f"{PREFIX}_result"] = _scan(period)
            except Exception as exc:
                st.error(f"盤點失敗：{exc}")
                return
        reset_select_all(PREFIX)
    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result["period"] != period:
        return

    remit = result["remit"]
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{
        RUN: checked(not r["done"], override, not r["done"]),
        "地區": r["area"], "金額": f"{r['total']:,}" if r["total"] else "",
        "來源": ("學院自己轉帳（只寫台北分錄）" if r.get("self_remit") else
                 "固定金額（內勤薪資）" if r["fixed"] else "行銷費用總表"),
        "狀態": "✅ " + r["detail"] if r["done"] else ("❌ 待寫分錄" if r.get("self_remit") else "❌ 待匯款"),
        "說明": r["memo"],
    } for r in remit])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != RUN])
    selected = [r for r, flag in zip(remit, edited[RUN]) if flag and not r["done"]]

    taichung = result["taichung"]
    electricity = None
    do_taichung = False
    if taichung["applicable"]:
        st.markdown("**台中固定費用請款（單月）**")
        if taichung["missing"]:
            st.warning("還沒新增：" + "、".join(taichung["missing"]))
            if taichung["electricity_missing"]:
                electricity = st.number_input("台中電費（前 2 個月合計，會除以 2 拆兩筆）", min_value=0, step=1,
                                              value=0, key=f"{PREFIX}_electricity")
            do_taichung = st.checkbox("一起新增台中固定費用請款", value=True, key=f"{PREFIX}_taichung")
        else:
            st.success("✅ 租金、電費都已新增到請款記錄")

    label = f"▶️ 執行：匯款 {len(selected)} 區" + ("＋台中固定費用請款" if do_taichung else "")
    if st.button(label, key=f"{PREFIX}_run", disabled=not (selected or do_taichung)):
        if do_taichung:
            from tools.finance_management.taichung_fixed_expense import submit_taichung_fixed_expenses

            if taichung["electricity_missing"] and not electricity:
                st.error("請先輸入台中電費金額")
                return
            try:
                # 電費已新增過就傳 0（只補租金）；要補電費時用輸入的金額
                power = int(electricity or 0) if taichung["electricity_missing"] else 0
                res = submit_taichung_fixed_expenses(period, power, "整合執行")
                st.success(f"台中固定費用請款：新增 {res['rows_added']} 筆")
            except Exception as exc:
                st.error(f"台中固定費用請款失敗：{exc}")
        for r in [r for r in selected if r.get("self_remit")]:
            from tools.finance_management.fixed_expense_transfer import transfer_fixed_expenses

            try:
                res = transfer_fixed_expenses(period, r["area"])
                st.success(f"{r['area']}：台北分錄新增 {res['statement_rows']} 列")
            except Exception as exc:
                st.error(f"{r['area']} 台北分錄寫入失敗：{exc}")
        selected = [r for r in selected if not r.get("self_remit")]
        for r in selected:
            task = create_task("fubon.fixed_expense_transfer",
                               {"area": r["area"], "month": period, "cdp_url": "http://127.0.0.1:9222"},
                               created_by=username or "各區固定費用匯款")
            log(f"已排入：{r['area']} 富邦匯款給台北 {r['total']:,}（{task.get('task_id', '')}）")
        if selected:
            st.success(f"已排入本機 Agent {len(selected)} 區，請依序在富邦核對後手機核准。完成後再按一次「盤點」確認。")
        st.session_state.pop(f"{PREFIX}_result", None)
