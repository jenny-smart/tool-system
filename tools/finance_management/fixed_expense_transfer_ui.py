"""【財報】各區固定費用匯款：勾選地區 → 盤點（各區第72列科目及金額）→ 勾選科目 → 執行。

更新紀錄：
  2026-11-09 Jenny：新增。原本「執行區域」下拉（全區／單一地區）改成地區勾選；先按「盤點」看各區科目及金額，
                    再勾選科目按「執行」。勾「執行富邦銀行匯款給台北」時，勾選的每一區各排一筆本機 Agent 匯款
                    （第74列已有匯款記錄的地區不能勾）；沒勾時直接新增台北／各區富邦更新分錄及各區請款記錄。
  2026-11-09 Jenny：盤點時：
                    - 先把「現金缺口試算」總營收（日期是該月時）寫進行銷費用總表該月各區第30列（營業額）
                    - 第74列已有匯款日期的地區只顯示已匯款；其餘 41～71 列有值的都列入（第72列沒金額也列）
                    - 表格多一欄「L欄」：寫進富邦更新的分類標籤
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import re

import pandas as pd
import streamlit as st

PREFIX = "fixed_expense_transfer"
RUN = "執行"
FUBON_AREAS = ("台中",)  # 2026-11-09 Jenny：目前只有台中用富邦匯給台北，其他地區只新增分錄


def _areas() -> tuple[str, ...]:
    from tools.finance_management.fixed_expense_transfer import AREAS

    return AREAS


def _paid_date(mark: str) -> str:
    """第74列的匯款日期（例 2026/10/13）；不是日期回傳空字串。"""
    found = re.search(r"\d{3,4}[./-]\d{1,2}[./-]\d{1,2}", mark or "")
    return found.group(0) if found else ""


def _scan(period: str, areas: list[str], log: Callable[[str], None]) -> dict:
    from tools.finance_management.fixed_expense_transfer import preview_fixed_expenses, sync_revenue_row

    revenue_note = ""
    try:
        written = sync_revenue_row(period, log)
        revenue_note = ("第30列營業額已更新：" + "、".join(f"{a} {v:,}" for a, v in written.items())) if written else \
            "現金缺口試算沒有本月總營收，第30列沒更新"
    except Exception as exc:
        revenue_note = f"⚠ 第30列營業額更新失敗：{exc}"
        log(revenue_note)
    plans, skipped = [], []
    for area in areas:
        preview = preview_fixed_expenses(period, area, lenient=True)
        plans += preview.get("plans") or []
        skipped += preview.get("skipped_areas") or []
    return {"period": period, "areas": list(areas), "plans": plans, "skipped": skipped, "revenue_note": revenue_note}


def _signed_total(items: list[dict]) -> int:
    return sum((-1 if item["subject"] == "稅捐" else 1) * int(item["amount"]) for item in items)


def render(create_task: Callable, log: Callable[[str], None], username: str = "") -> None:
    period = st.text_input("期別（YYYYMM，預設本月）", value=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m"),
                           key=f"{PREFIX}_period").strip()
    execute_fubon = st.checkbox("執行富邦銀行匯款給台北", value=False, key=f"{PREFIX}_fubon",
                                help="銀行端仍需人工核對並按確認；成功後才會同步明細與原訂分錄。")
    if execute_fubon:
        st.caption("富邦匯款只有台中（其他地區照樣只新增分錄）；第74列空白才會匯。"
                   "順序：富邦匯款 → 該區富邦更新 → 原訂分錄。")
    else:
        st.caption("篩選第72列 > 0，新增台北／各區富邦更新分錄及各區請款記錄。")
    st.caption("每月 10 日 04:00 排程（排程設定 fixed_expense_remit）會自動盤點高雄以外各區並排入富邦匯款。")

    st.markdown("**地區**")
    columns = st.columns(len(_areas()))
    areas = [area for area, column in zip(_areas(), columns)
             if column.checkbox(area, value=True, key=f"{PREFIX}_area_{area}")]

    if st.button("🔍 盤點", key=f"{PREFIX}_scan", type="primary", disabled=not areas):
        if len(period) != 6 or not period.isdigit():
            st.error("期別請輸入 YYYYMM，例如 202611")
            return
        with st.spinner("讀取行銷費用總表…"):
            try:
                st.session_state[f"{PREFIX}_result"] = _scan(period, areas, log)
            except Exception as exc:
                st.error(f"盤點失敗：{exc}")
                return
    result = st.session_state.get(f"{PREFIX}_result")
    if not result or result["period"] != period or result["areas"] != areas:
        if areas:
            st.info("勾選地區後按「盤點」。")
        return

    if result.get("revenue_note"):
        st.caption(result["revenue_note"])
    st.markdown("**📋 該月相關科目及金額**")
    selected: dict[str, list[int]] = {}
    for plan in result["plans"]:
        area = str(plan["area"])
        destination = f" → {plan['report_area']}財報" if plan.get("report_area") not in (None, area) else ""
        st.caption(f"{area}{destination}｜第72列匯款金額：{int(plan['total']):,}")
        mark = str(plan.get("transfer_mark") or "").strip()
        paid = _paid_date(mark)
        if paid:
            st.success(f"第74列已有匯款日期 {mark}，不列入。")
            continue
        if mark:
            st.caption(f"第74列匯款記錄：{mark}")
        if plan.get("mismatch"):
            st.warning(plan["mismatch"] + "；請確認勾選的科目，執行時依勾選列重算金額。")
        labels = [str(row[11]) for row in plan.get("taipei_rows") or []]
        table = pd.DataFrame([{RUN: True, "來源": item.get("source_area", area), "來源列": int(item["row_number"]),
                               "科目": item["subject"], "金額": int(item["amount"]),
                               "L欄": labels[i] if i < len(labels) else ""}
                              for i, item in enumerate(plan["items"])])
        edited = st.data_editor(
            table, hide_index=True, width="stretch", disabled=["來源", "來源列", "科目", "金額", "L欄"],
            column_config={RUN: st.column_config.CheckboxColumn(RUN, default=True, required=True), "來源列": None,
                           "金額": st.column_config.NumberColumn("金額", format="%,d")},
            key=f"{PREFIX}_subjects_{period}_{area}")
        rows = [int(r) for r in edited.loc[edited[RUN], "來源列"].tolist()]
        picked = [item for item in plan["items"] if int(item["row_number"]) in rows]
        if picked:
            st.caption(f"已勾選匯款金額：{_signed_total(picked):,}")
            selected[area] = rows
        else:
            st.warning("請至少勾選一個科目。")
    if not result["plans"]:
        st.info("勾選的地區該期別都沒有第72列 > 0 的固定費用資料。")
    if result["skipped"]:
        st.caption("未符合條件：" + "、".join(result["skipped"]))

    fubon = [a for a in selected if execute_fubon and a in FUBON_AREAS]
    entries = [a for a in selected if a not in fubon]
    label = "▶️ 執行：" + "＋".join(part for part in (
        f"富邦匯款 {'、'.join(fubon)}" if fubon else "", f"新增分錄 {len(entries)} 區" if entries else "") if part)
    if st.button(label if selected else "▶️ 執行", key=f"{PREFIX}_run", disabled=not selected):
        _run(period, selected, execute_fubon, create_task, log, username)
        st.session_state.pop(f"{PREFIX}_result", None)


def _run(period: str, selected: dict[str, list[int]], execute_fubon: bool, create_task: Callable,
         log: Callable[[str], None], username: str) -> None:
    from tools.finance_management.fixed_expense_transfer import (
        preview_fixed_expenses,
        require_fubon_not_transferred,
        transfer_fixed_expenses,
    )

    for area, rows in selected.items():
        try:
            if execute_fubon and area in FUBON_AREAS:
                plans = preview_fixed_expenses(period, area, lenient=True).get("plans") or []
                if not plans:
                    raise ValueError(f"{period}／{area} 第72列沒有可匯款金額")
                require_fubon_not_transferred(plans[0], period, area)
                task = create_task("fubon.fixed_expense_transfer",
                                   {"area": area, "month": period, "selected_rows": rows,
                                    "cdp_url": "http://127.0.0.1:9222"},
                                   created_by=username or "各區固定費用匯款")
                log(f"已排入：{area} 富邦匯款給台北（{task.get('task_id', '')}）")
                st.success(f"{area}：富邦匯款任務已建立，請在富邦核對後按確認；成功後會同步明細及原訂分錄。")
            else:
                res = transfer_fixed_expenses(period, area=area, on_progress=log, selected_rows={area: rows})
                lines = res["summaries"] or ["沒有第72列 > 0 的資料"]
                log("；".join(lines))
                st.success("；".join(lines))
        except Exception as exc:
            log(f"{area} 執行失敗：{exc}")
            st.error(f"{area} 執行失敗：{exc}")
