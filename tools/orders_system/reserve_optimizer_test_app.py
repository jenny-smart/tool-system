# -*- coding: utf-8 -*-
"""大掃除保留單 - 測試機專用 Streamlit 入口。"""

from datetime import date

import pandas as pd
import streamlit as st

from accounts import ACCOUNTS
from orders import get_region_by_address
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
st.warning("此頁固定使用 dev 測試機。會真的成立訂單，但不寄確認信。請先完成定期客排班與檸檬人真人化。")

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

st.subheader("期間與保留率")
d1, d2 = st.columns(2)
with d1:
    start = st.date_input("開始日期", value=date(2026, 9, 21))
with d2:
    end = st.date_input("結束日期", value=date(2026, 9, 30))

periods = st.multiselect(
    "要處理的時段",
    list(PERIOD_HOURS.keys()),
    default=["08:30-12:30", "09:00-12:00", "14:00-18:00"],
)

st.caption("目前一段期間可設定一組 AM/PM 比例；之後可再增加多區間規則表。")
r1, r2 = st.columns(2)
with r1:
    am_rate = st.slider("AM 保留率", 0, 100, 70, 5) / 100.0
with r2:
    pm_rate = st.slider("PM 保留率", 0, 100, 70, 5) / 100.0

rules = [ReserveRule(start=start, end=end, am_rate=am_rate, pm_rate=pm_rate, label="測試區間")]

if st.button("2. 統計整段班表並產生保留計畫", use_container_width=True):
    try:
        plan = build_period_plan(lookup, start, end, rules, periods)
        st.session_state.reserve_plan = [p.__dict__ for p in plan]
    except Exception as exc:
        st.error(f"讀取班表失敗：{exc}")

plan_rows = st.session_state.get("reserve_plan") or []
if not plan_rows:
    st.stop()

df = pd.DataFrame(plan_rows)
show = df[[
    "service_date", "period", "unassigned_people", "reserve_rate",
    "reserve_people_target", "reserve_order_target", "market_people_target",
]].copy()
show["reserve_rate"] = (show["reserve_rate"] * 100).round(0).astype(int).astype(str) + "%"
show.columns = ["日期", "時段", "未配班人數", "保留率", "預計保留人數", "預計保留單", "預計留給市場"]
st.dataframe(show, use_container_width=True, hide_index=True)

total_orders = int(df["reserve_order_target"].sum())
total_people = int(df["reserve_people_target"].sum())
st.info(f"本次期間 {start}～{end}：預計建立 {total_orders} 張保留單，共保留 {total_people} 位人力。每個日期/時段會保留表格中的『預計留給市場』人數。")

st.subheader("A. 整段期間一次建立")
st.warning("按下後會依上方整張計畫表，從第一天到最後一天逐張真的建立測試機訂單。每張前會重查班表；某時段人力不足會停止該時段，但繼續下一個日期/時段。")
confirm_all = st.checkbox(f"我確認要在測試機一次建立 {start}～{end} 的全部建議保留單（預計 {total_orders} 張）", key="confirm_all")
if st.button("3A. 確認建立整段期間保留單", type="primary", use_container_width=True, disabled=not confirm_all or total_orders <= 0):
    try:
        with st.spinner(f"正在測試機建立 {start}～{end} 保留單，請勿關閉頁面..."):
            result = create_reserve_orders_for_plan(
                env_name="dev",
                lookup_result=lookup,
                region=region,
                address=address,
                plan_rows=plan_rows,
                payway=payway,
                continue_after_slot_error=True,
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
            with st.spinner("正在測試機逐張成立保留訂單..."):
                result = create_reserve_orders_for_slot(
                    env_name="dev",
                    lookup_result=lookup,
                    region=region,
                    address=address,
                    service_date=row["service_date"],
                    period=row["period"],
                    reserve_rate=float(row["reserve_rate"]),
                    target_orders=int(execute_count),
                    payway=payway,
                    stop_when_market_people_below=int(market_floor),
                )
            st.session_state.reserve_last_run = result
        except Exception as exc:
            st.error(str(exc))

last_run = st.session_state.get("reserve_last_run")
if last_run:
    st.success(f"批次 {last_run['batch_id']}：成功 {last_run['success_count']} / {last_run['target_orders']} 張")
    results = last_run.get("results") or []
    if results:
        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
