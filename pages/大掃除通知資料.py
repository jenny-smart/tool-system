from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from tools.service_management.deep_clean_notice import generate_notice_data


TW_TZ = ZoneInfo("Asia/Taipei")
st.set_page_config(page_title="大掃除通知資料", page_icon="🧹", layout="centered")
st.title("🧹 大掃除通知資料")
st.caption("沿用客服排程的 VIP Google Calendar 與儲值金 LINE 連結；只建立／更新大掃除通知資料，不修改原始日曆或 VIP 資料。")

today = datetime.now(TW_TZ).date()
next_year = today.year + 1

area = st.selectbox("區域", ["全區", "台北", "台中"])

st.markdown("### 第一階段")
p1c1, p1c2, p1c3 = st.columns([1, 1, 1])
with p1c1:
    phase1_start = st.date_input("開始日期", value=today, key="deep_clean_p1_start")
with p1c2:
    phase1_end = st.date_input("結束日期", value=today + timedelta(days=30), key="deep_clean_p1_end")
with p1c3:
    phase1_rate = st.number_input("每 2 人 1 小時加價", min_value=0, step=50, value=0, key="deep_clean_p1_rate")

st.markdown("### 第二階段")
p2c1, p2c2, p2c3 = st.columns([1, 1, 1])
with p2c1:
    phase2_start = st.date_input("開始日期", value=today + timedelta(days=31), key="deep_clean_p2_start")
with p2c2:
    phase2_end = st.date_input("結束日期", value=today + timedelta(days=60), key="deep_clean_p2_end")
with p2c3:
    phase2_rate = st.number_input("每 2 人 1 小時加價", min_value=0, step=50, value=0, key="deep_clean_p2_rate")

st.info("產出後會在客服排程目標試算表建立「YYYY大掃除通知資料」。每位客戶會有完整通知文字與「開啟 LINE」連結；通知文字可直接複製，再點 LINE 連結通知客戶。")

if st.button("產生大掃除通知資料", type="primary", use_container_width=True):
    if phase1_start > phase1_end or phase2_start > phase2_end:
        st.error("階段開始日期不可晚於結束日期")
    else:
        with st.spinner("正在讀取 VIP 日曆並整理通知資料…"):
            try:
                def dt(value, end=False):
                    return datetime.combine(
                        value,
                        datetime.max.time() if end else datetime.min.time(),
                        tzinfo=TW_TZ,
                    )

                result = generate_notice_data(
                    area,
                    dt(phase1_start),
                    dt(phase1_end, True),
                    dt(phase2_start),
                    dt(phase2_end, True),
                    float(phase1_rate),
                    float(phase2_rate),
                )
                detail = "、".join(f"{name} {count} 位" for name, count in result["areas"].items())
                st.success(f"完成：{result['sheet']}，共 {result['count']} 位客戶（{detail}）")
            except Exception as exc:
                st.exception(exc)
