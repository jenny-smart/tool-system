# -*- coding: utf-8 -*-
"""大掃除保留單 - 測試機專用 Streamlit 入口。"""

from datetime import date

import pandas as pd
import streamlit as st

from accounts import ACCOUNTS
from orders import get_region_by_address
from reserve_cancel import SYSTEM_RESERVE_MEMO, cancel_reserve_orders, find_reserve_orders
from reserve_optimizer import (
    PERIOD_HOURS,
    RESERVE_PHONE_DEFAULT,
    ReserveRule,
    build_period_plan,
    create_reserve_orders_for_plan,
    create_reserve_orders_for_slot,
    login_reserve_member,
    member_addresses,
)

st.set_page_config(page_title="大掃除保留單測試", page_icon="🍋", layout="wide")
st.title("🍋 大掃除保留單（測試機）")
st.warning("此頁固定使用 dev 測試機。建立與取消都會真的異動測試機訂單。")

c1, c2 = st.columns(2)
with c1:
    email = st.text_input("後台帳號")
with c2:
    password = st.text_input("後台密碼", type="password")
phone = st.text_input("保留單會員手機", value=RESERVE_PHONE_DEFAULT)

if "reserve_lookup" not in st.session_state:
    st.session_state.reserve_lookup = None

if st.button("1. 登入並讀取保留會員", use_container_width=True):
    try:
        st.session_state.reserve_lookup = login_reserve_member("dev", email.strip(), password.strip(), phone.strip())
        st.success("測試機登入成功，已讀取保留會員。")
    except Exception as exc:
        st.session_state.reserve_lookup = None
        st.error(str(exc))

lookup = st.session_state.reserve_lookup
if not lookup:
    st.stop()

addresses = member_addresses(lookup)
if not addresses:
    st.error("這支保留會員手機在測試機沒有可用地址，請先到後台建立地址。")
    st.stop()

address = st.selectbox("保留單服務地址", addresses)
region = get_region_by_address(address, ACCOUNTS) or "台北"
st.caption(f"地址判斷區域：{region}")
payway = st.selectbox("保留單付款類型", ["儲值金", "信用卡", "ATM"], index=0)

tab_create, tab_cancel = st.tabs(["建立保留單", "取消保留單"])

with tab_create:
    st.subheader("分析期間與保留率")
    d1, d2 = st.columns(2)
    with d1:
        start = st.date_input("分析開始日期", value=date(2026, 9, 21), key="plan_start")
    with d2:
        end = st.date_input("分析結束日期", value=date(2026, 9, 30), key="plan_end")

    periods = st.multiselect(
        "要分析的時段",
        list(PERIOD_HOURS.keys()),
        default=["08:30-12:30", "09:00-12:00", "14:00-18:00"],
        key="plan_periods",
    )
    st.caption("分析期間只用來看完整人力。真正建立時可再自行選其中一段日期。")

    r1, r2 = st.columns(2)
    with r1:
        am_rate = st.slider("AM 保留率", 0, 100, 70, 5) / 100.0
    with r2:
        pm_rate = st.slider("PM 保留率", 0, 100, 70, 5) / 100.0

    rules = [ReserveRule(start=start, end=end, am_rate=am_rate, pm_rate=pm_rate, label="測試區間")]

    if st.button("2. 統計班表並產生保留計畫", use_container_width=True):
        try:
            plan = build_period_plan(lookup, start, end, rules, periods)
            st.session_state.reserve_plan = [p.__dict__ for p in plan]
        except Exception as exc:
            st.error(f"讀取班表失敗：{exc}")

    plan_rows = st.session_state.get("reserve_plan") or []
    if plan_rows:
        df = pd.DataFrame(plan_rows)
        show = df[[
            "service_date", "period", "unassigned_people", "reserve_rate",
            "reserve_people_target", "reserve_order_target", "market_people_target",
        ]].copy()
        show["reserve_rate"] = (show["reserve_rate"] * 100).round(0).astype(int).astype(str) + "%"
        show.columns = ["日期", "時段", "未配班人數", "保留率", "預計保留人數", "預計保留單", "預計留給市場"]
        st.dataframe(show, use_container_width=True, hide_index=True)

        st.subheader("A. 自選期間批次建立")
        e1, e2 = st.columns(2)
        with e1:
            execute_start = st.date_input("執行開始日期", value=start, min_value=start, max_value=end, key="execute_start")
        with e2:
            execute_end = st.date_input("執行結束日期", value=end, min_value=start, max_value=end, key="execute_end")

        if execute_end < execute_start:
            st.error("執行結束日期不可早於執行開始日期。")
            selected_rows = []
        else:
            selected_rows = [
                r for r in plan_rows
                if execute_start.isoformat() <= str(r.get("service_date") or "") <= execute_end.isoformat()
            ]

        selected_df = pd.DataFrame(selected_rows)
        selected_total_orders = int(selected_df["reserve_order_target"].sum()) if not selected_df.empty else 0
        selected_total_people = int(selected_df["reserve_people_target"].sum()) if not selected_df.empty else 0
        st.info(f"你選擇 {execute_start}～{execute_end}：預計建立 {selected_total_orders} 張，共保留 {selected_total_people} 位人力。新建訂單的客人備註會自動寫入『{SYSTEM_RESERVE_MEMO}』。")

        if not selected_df.empty:
            selected_show = selected_df[["service_date", "period", "reserve_order_target", "market_people_target"]].copy()
            selected_show.columns = ["日期", "時段", "預計建立保留單", "至少留給市場"]
            st.dataframe(selected_show, use_container_width=True, hide_index=True)

        confirm_all = st.checkbox(
            f"我確認要在測試機建立 {execute_start}～{execute_end} 的建議保留單（預計 {selected_total_orders} 張）",
            key="confirm_all",
        )
        if st.button("3A. 確認建立自選期間保留單", type="primary", use_container_width=True, disabled=not confirm_all or selected_total_orders <= 0):
            try:
                with st.spinner(f"正在建立 {execute_start}～{execute_end} 保留單..."):
                    result = create_reserve_orders_for_plan(
                        env_name="dev", lookup_result=lookup, region=region, address=address,
                        plan_rows=selected_rows, payway=payway, continue_after_slot_error=True,
                    )
                st.session_state.reserve_last_run = result
            except Exception as exc:
                st.error(str(exc))

        with st.expander("B. 單一日期/時段測試", expanded=False):
            labels = [f"{r['service_date']}｜{r['period']}｜保留 {r['reserve_order_target']} 張" for r in plan_rows]
            selected = st.selectbox("選擇要執行的日期/時段", range(len(labels)), format_func=lambda i: labels[i])
            row = plan_rows[selected]
            max_orders = int(row["reserve_order_target"])
            execute_count = st.number_input("本次真的建立幾張", min_value=0, max_value=max_orders, value=min(1, max_orders), step=1)
            market_floor = st.number_input("至少保留幾位未配班真人給市場", min_value=0, value=int(row["market_people_target"]), step=1)
            confirm_one = st.checkbox("我確認目前是測試機，且這次要真的成立這個時段的保留訂單", key="confirm_one")
            if st.button("3B. 建立單一時段保留單", use_container_width=True, disabled=not confirm_one or execute_count <= 0):
                try:
                    result = create_reserve_orders_for_slot(
                        env_name="dev", lookup_result=lookup, region=region, address=address,
                        service_date=row["service_date"], period=row["period"], reserve_rate=float(row["reserve_rate"]),
                        target_orders=int(execute_count), payway=payway, stop_when_market_people_below=int(market_floor),
                    )
                    st.session_state.reserve_last_run = result
                except Exception as exc:
                    st.error(str(exc))

        last_run = st.session_state.get("reserve_last_run")
        if last_run:
            st.success(f"建立批次 {last_run['batch_id']}：成功 {last_run['success_count']} / {last_run['target_orders']} 張")
            if last_run.get("results"):
                st.dataframe(pd.DataFrame(last_run["results"]), use_container_width=True, hide_index=True)

with tab_cancel:
    st.subheader("取消保留單")
    st.info("先選期間、時段與客人備註篩選，再決定要取消幾張。真正取消前會再次讀取最新客人備註；若已被改成人工客人保留內容，就會自動跳過。")

    c1, c2 = st.columns(2)
    with c1:
        cancel_start = st.date_input("取消查詢開始日期", value=date(2026, 9, 21), key="cancel_start")
    with c2:
        cancel_end = st.date_input("取消查詢結束日期", value=date(2026, 9, 30), key="cancel_end")

    cancel_periods = st.multiselect(
        "取消查詢時段（不選代表全部時段）",
        list(PERIOD_HOURS.keys()),
        default=[],
        key="cancel_periods",
    )
    memo_filter = st.selectbox(
        "客人備註篩選",
        ["僅系統保留單", "系統保留單或空白", "僅空白", "全部（僅供查看）"],
        index=0,
    )

    if st.button("4. 查詢可取消保留單", use_container_width=True):
        try:
            rows = find_reserve_orders(
                "dev", email.strip(), password.strip(), phone.strip(),
                cancel_start.isoformat(), cancel_end.isoformat(),
                memo_filter=memo_filter, periods=cancel_periods,
            )
            st.session_state.reserve_cancel_rows = rows
            st.session_state.reserve_cancel_filter = memo_filter
        except Exception as exc:
            st.session_state.reserve_cancel_rows = []
            st.error(str(exc))

    cancel_rows = st.session_state.get("reserve_cancel_rows") or []
    if cancel_rows:
        cancel_df = pd.DataFrame(cancel_rows)
        columns = [c for c in ["service_date", "period", "order_no", "payment_status", "customer_memo", "cancel_eligible"] if c in cancel_df.columns]
        preview = cancel_df[columns].copy()
        preview.columns = {
            "service_date": "日期", "period": "時段", "order_no": "訂單編號",
            "payment_status": "付款狀態", "customer_memo": "客人備註", "cancel_eligible": "安全可取消",
        }.values()
        st.dataframe(preview, use_container_width=True, hide_index=True)

        safe_rows = [r for r in cancel_rows if r.get("cancel_eligible")]
        st.write(f"查到 **{len(cancel_rows)} 張**；其中目前符合安全取消條件 **{len(safe_rows)} 張**。")

        if st.session_state.get("reserve_cancel_filter") == "全部（僅供查看）":
            st.warning("目前使用『全部（僅供查看）』，此模式不開放取消。請改選『僅系統保留單』、『系統保留單或空白』或『僅空白』後重新查詢。")
        elif safe_rows:
            cancel_count = st.number_input(
                "這次要取消幾張",
                min_value=1,
                max_value=len(safe_rows),
                value=1,
                step=1,
                key="cancel_count",
            )
            to_cancel = safe_rows[:int(cancel_count)]
            st.caption("系統會依日期、時段、訂單編號排序，從下列預覽最前面開始取消。")
            preview_cancel = pd.DataFrame(to_cancel)
            cols = [c for c in ["service_date", "period", "order_no", "customer_memo"] if c in preview_cancel.columns]
            st.dataframe(preview_cancel[cols], use_container_width=True, hide_index=True)

            confirm_cancel = st.checkbox(
                f"我確認要在測試機取消以上 {int(cancel_count)} 張保留單",
                key="confirm_cancel",
            )
            if st.button("5. 確認取消保留單", type="primary", use_container_width=True, disabled=not confirm_cancel):
                try:
                    with st.spinner("正在逐張重新檢查客人備註並取消..."):
                        results = cancel_reserve_orders(
                            "dev", email.strip(), password.strip(), safe_rows, int(cancel_count)
                        )
                    st.session_state.reserve_cancel_result = results
                    st.success("取消流程執行完成；有人工備註的訂單會顯示為跳過。")
                except Exception as exc:
                    st.error(str(exc))
    else:
        if "reserve_cancel_rows" in st.session_state:
            st.warning("此期間與篩選條件沒有查到符合的保留單。")

    cancel_result = st.session_state.get("reserve_cancel_result") or []
    if cancel_result:
        st.dataframe(pd.DataFrame(cancel_result), use_container_width=True, hide_index=True)
