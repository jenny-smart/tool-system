# -*- coding: utf-8 -*-
"""服務訂單系統內的檸檬保留單建單／取消 UI。"""

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
    login_reserve_member,
    member_addresses,
)


def _require_supported_env(env: str) -> bool:
    if str(env or "").strip().lower() not in {"prod", "dev"}:
        st.error("檸檬保留單只支援 prod 正式機或 dev 測試機。")
        return False
    return True


def _env_label(env: str) -> str:
    return "正式機 prod" if str(env).lower() == "prod" else "測試機 dev"


def _lookup(env: str, backend_email: str, backend_password: str, phone: str):
    key = f"reserve_lookup::{env}::{phone}"
    cached = st.session_state.get(key)
    if cached:
        return cached
    result = login_reserve_member(env, backend_email.strip(), backend_password.strip(), phone.strip())
    st.session_state[key] = result
    return result


def _common_member_header(env: str, backend_email: str, backend_password: str):
    phone = st.text_input("保留單會員手機", value=RESERVE_PHONE_DEFAULT, key="reserve_shared_phone")
    st.info(f"目前執行環境：{_env_label(env)}")
    if not backend_email.strip() or not backend_password.strip():
        st.warning("請先在頁面上方輸入後台帳號與密碼。")
        return phone, None
    try:
        lookup = _lookup(env, backend_email, backend_password, phone)
    except Exception as exc:
        st.error(f"讀取保留會員失敗：{exc}")
        return phone, None
    return phone, lookup


def render_reserve_create(backend_email: str, backend_password: str, env: str) -> None:
    st.markdown("### 🍋 檸檬保留單建單")
    st.caption("依期間統計未配班人力、設定 AM/PM 保留率，再選擇真正要成立訂單的日期範圍。")
    if not _require_supported_env(env):
        return

    phone, lookup = _common_member_header(env, backend_email, backend_password)
    if not lookup:
        return

    addresses = member_addresses(lookup)
    if not addresses:
        st.error(f"保留會員 {phone} 在{_env_label(env)}沒有可用地址。")
        return

    c1, c2 = st.columns(2)
    with c1:
        address = st.selectbox("保留單服務地址", addresses, key="reserve_create_address")
    with c2:
        payway = st.selectbox("保留單付款類型", ["儲值金", "信用卡", "ATM"], index=0, key="reserve_create_payway")
    region = get_region_by_address(address, ACCOUNTS) or "台北"
    st.caption(f"地址判斷區域：{region}｜客人備註會自動寫入「{SYSTEM_RESERVE_MEMO}」｜客服備註固定「大掃除檸檬保留單」")

    st.markdown("#### 1. 分析期間與保留率")
    d1, d2 = st.columns(2)
    with d1:
        start = st.date_input("分析開始日期", value=date(2026, 9, 21), key="reserve_plan_start")
    with d2:
        end = st.date_input("分析結束日期", value=date(2026, 9, 30), key="reserve_plan_end")
    periods = st.multiselect("要分析的時段", list(PERIOD_HOURS.keys()), default=["09:00-12:00", "14:00-17:00"], key="reserve_plan_periods")
    r1, r2 = st.columns(2)
    with r1:
        am_rate = st.slider("AM 保留率", 0, 100, 70, 5, key="reserve_am_rate") / 100.0
    with r2:
        pm_rate = st.slider("PM 保留率", 0, 100, 70, 5, key="reserve_pm_rate") / 100.0

    if st.button("統計班表並產生保留計畫", width="stretch", key="reserve_build_plan"):
        try:
            rules = [ReserveRule(start=start, end=end, am_rate=am_rate, pm_rate=pm_rate, label="保留區間")]
            plan = build_period_plan(lookup, start, end, rules, periods)
            st.session_state.reserve_menu_plan = [p.__dict__ for p in plan]
        except Exception as exc:
            st.error(f"讀取班表失敗：{exc}")

    plan_rows = st.session_state.get("reserve_menu_plan") or []
    if not plan_rows:
        return

    df = pd.DataFrame(plan_rows)
    show = df[["service_date", "period", "unassigned_people", "reserve_rate", "reserve_people_target", "reserve_order_target", "market_people_target"]].copy()
    show["reserve_rate"] = (show["reserve_rate"] * 100).round(0).astype(int).astype(str) + "%"
    show.columns = ["日期", "時段", "未配班人數", "保留率", "預計保留人數", "預計保留單", "預計留給市場"]
    st.dataframe(show, width="stretch", hide_index=True)

    st.markdown("#### 2. 自選真正建單期間")
    e1, e2 = st.columns(2)
    with e1:
        execute_start = st.date_input("執行開始日期", value=start, min_value=start, max_value=end, key="reserve_execute_start")
    with e2:
        execute_end = st.date_input("執行結束日期", value=end, min_value=start, max_value=end, key="reserve_execute_end")
    if execute_end < execute_start:
        st.error("執行結束日期不可早於開始日期。")
        return

    selected_rows = [r for r in plan_rows if execute_start.isoformat() <= str(r.get("service_date") or "") <= execute_end.isoformat()]
    selected_df = pd.DataFrame(selected_rows)
    total_orders = int(selected_df["reserve_order_target"].sum()) if not selected_df.empty else 0
    total_people = int(selected_df["reserve_people_target"].sum()) if not selected_df.empty else 0
    st.info(f"{execute_start}～{execute_end} 預計建立 {total_orders} 張保留單，共保留 {total_people} 位人力。")

    machine = _env_label(env)
    confirm = st.checkbox(f"我確認要在{machine}建立以上 {total_orders} 張檸檬保留單", key="reserve_create_confirm")
    if st.button("確認建立檸檬保留單", type="primary", width="stretch", disabled=not confirm or total_orders <= 0, key="reserve_create_execute"):
        try:
            with st.spinner(f"正在{machine}逐張重新確認班表並建立保留單..."):
                result = create_reserve_orders_for_plan(env_name=env, lookup_result=lookup, region=region, address=address, plan_rows=selected_rows, payway=payway, continue_after_slot_error=True)
            st.session_state.reserve_menu_last_create = result
            st.success(f"建立完成：成功 {result.get('success_count', 0)} / {result.get('target_orders', 0)} 張")
        except Exception as exc:
            st.error(str(exc))

    result = st.session_state.get("reserve_menu_last_create")
    if result and result.get("results"):
        st.dataframe(pd.DataFrame(result["results"]), width="stretch", hide_index=True)


def render_reserve_cancel(backend_email: str, backend_password: str, env: str) -> None:
    st.markdown("### 🗑️ 檸檬保留單取消")
    st.caption("依期間、複選時段與客人備註篩選保留單，再指定取消張數。取消前會重新檢查最新客人備註。")
    if not _require_supported_env(env):
        return

    phone, lookup = _common_member_header(env, backend_email, backend_password)
    if not lookup:
        return

    c1, c2 = st.columns(2)
    with c1:
        cancel_start = st.date_input("取消查詢開始日期", value=date(2026, 9, 21), key="reserve_cancel_start")
    with c2:
        cancel_end = st.date_input("取消查詢結束日期", value=date(2026, 9, 30), key="reserve_cancel_end")

    cancel_periods = st.multiselect("取消查詢時段（可複選；不選代表全部時段）", list(PERIOD_HOURS.keys()), default=[], key="reserve_cancel_periods")
    memo_filter = st.selectbox("客人備註篩選", ["僅系統保留單", "系統保留單或空白", "僅空白", "全部（僅供查看）"], index=0, key="reserve_cancel_memo_filter")

    if st.button("查詢可取消保留單", width="stretch", key="reserve_cancel_search"):
        try:
            rows = find_reserve_orders(env, backend_email.strip(), backend_password.strip(), phone.strip(), cancel_start.isoformat(), cancel_end.isoformat(), memo_filter=memo_filter, periods=cancel_periods)
            st.session_state.reserve_menu_cancel_rows = rows
            st.session_state.reserve_menu_cancel_filter = memo_filter
        except Exception as exc:
            st.session_state.reserve_menu_cancel_rows = []
            st.error(str(exc))

    rows = st.session_state.get("reserve_menu_cancel_rows") or []
    if not rows:
        if "reserve_menu_cancel_rows" in st.session_state:
            st.warning("此期間與篩選條件沒有查到符合的保留單。")
        return

    df = pd.DataFrame(rows)
    cols = [c for c in ["service_date", "period", "order_no", "customer_memo", "cancel_eligible"] if c in df.columns]
    preview = df[cols].copy()
    preview.rename(columns={"service_date": "日期", "period": "時段", "order_no": "訂單編號", "customer_memo": "客人備註", "cancel_eligible": "安全可取消"}, inplace=True)
    st.dataframe(preview, width="stretch", hide_index=True)

    safe_rows = [r for r in rows if r.get("cancel_eligible")]
    st.write(f"查到 **{len(rows)} 張**；目前符合安全取消條件 **{len(safe_rows)} 張**。")
    if st.session_state.get("reserve_menu_cancel_filter") == "全部（僅供查看）":
        st.warning("『全部（僅供查看）』不開放取消，請改用其他客人備註篩選後重新查詢。")
        return
    if not safe_rows:
        return

    count = st.number_input("這次要取消幾張", min_value=1, max_value=len(safe_rows), value=1, step=1, key="reserve_cancel_count")
    selected = safe_rows[: int(count)]
    selected_df = pd.DataFrame(selected)
    cols2 = [c for c in ["service_date", "period", "order_no", "customer_memo"] if c in selected_df.columns]
    st.caption("依日期、時段、訂單編號排序，將取消下列最前面的訂單：")
    st.dataframe(selected_df[cols2], width="stretch", hide_index=True)

    machine = _env_label(env)
    confirm = st.checkbox(f"我確認要在{machine}取消以上 {int(count)} 張檸檬保留單", key="reserve_cancel_confirm")
    if st.button("確認取消檸檬保留單", type="primary", width="stretch", disabled=not confirm, key="reserve_cancel_execute"):
        try:
            with st.spinner(f"正在{machine}逐張重新檢查客人備註並取消..."):
                results = cancel_reserve_orders(env, backend_email.strip(), backend_password.strip(), safe_rows, int(count))
            st.session_state.reserve_menu_cancel_result = results
            st.success("取消流程執行完成；若備註已被改成人工客人保留內容，該筆會自動跳過。")
        except Exception as exc:
            st.error(str(exc))

    results = st.session_state.get("reserve_menu_cancel_result") or []
    if results:
        st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)
