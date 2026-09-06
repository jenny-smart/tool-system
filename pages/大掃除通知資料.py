from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from tools.service_management.deep_clean_notice import (
    build_nonroutine_notice,
    generate_notice_data,
    save_nonroutine_notice,
)


TW_TZ = ZoneInfo("Asia/Taipei")
st.set_page_config(page_title="大掃除通知資料", page_icon="🧹", layout="centered")
st.title("🧹 2026–2027 大掃除通知")
st.caption("產生定期／非定期 VIP 的 Email 合併資料與 LINE 通知文案；不會自動寄信。")

area = st.selectbox("區域", ["全區", "台北", "台中"])
target_spreadsheet_id = st.text_input(
    "通知輸出試算表 ID（空白則沿用客服排程設定）",
    help="輸出分頁包含 Email、主旨、完整內文、LINE 連結、寄送狀態與寄送時間。",
)

st.markdown("### 大掃除期間與價格")
p1c1, p1c2 = st.columns(2)
with p1c1:
    phase1_start = st.date_input("PART 1 開始", value=date(2026, 12, 15))
    phase1_weekday_rate = st.number_input("PART 1 平日加價", min_value=0, step=50, value=0)
with p1c2:
    phase1_end = st.date_input("PART 1 結束", value=date(2027, 1, 21))
    phase1_weekend_rate = st.number_input("PART 1 週六＋週日加價", min_value=0, step=50, value=0)

p2c1, p2c2 = st.columns(2)
with p2c1:
    phase2_start = st.date_input("PART 2 開始", value=date(2027, 1, 22))
    phase2_weekday_rate = st.number_input("PART 2 平日加價", min_value=0, step=50, value=0)
with p2c2:
    phase2_end = st.date_input("PART 2 結束", value=date(2027, 2, 4))
    phase2_weekend_rate = st.number_input("PART 2 週六＋週日加價", min_value=0, step=50, value=0)

st.info("價格尚未決定時可保留 0；四項價格填妥後才允許正式產出，避免誤用去年價格。")


def dt(value: date, end: bool = False) -> datetime:
    return datetime.combine(value, datetime.max.time() if end else datetime.min.time(), tzinfo=TW_TZ)


def rates_ready() -> bool:
    return all(value > 0 for value in (
        phase1_weekday_rate, phase1_weekend_rate, phase2_weekday_rate, phase2_weekend_rate,
    ))


regular_tab, nonroutine_tab = st.tabs(["定期 VIP", "非定期 VIP"])

with regular_tab:
    reply_deadline = st.text_input(
        "定期 VIP 回覆截止時間（未定可留白）",
        placeholder="例如：2026/11/03（二）17:00",
    )
    st.write("系統會讀取 Calendar 排程，計算兩階段次數、加價及年後第一次服務，並建立 Email 合併清單。")
    if st.button("產生定期 VIP 通知清單", type="primary", use_container_width=True):
        if not rates_ready():
            st.error("請先填妥四項年節加價")
        elif phase1_start > phase1_end or phase2_start > phase2_end or phase1_end >= phase2_start:
            st.error("請確認兩階段日期順序")
        else:
            with st.spinner("正在讀取 VIP 排程並產生 Email／LINE 通知資料…"):
                try:
                    result = generate_notice_data(
                        area,
                        dt(phase1_start), dt(phase1_end, True),
                        dt(phase2_start), dt(phase2_end, True),
                        float(phase1_weekday_rate), float(phase1_weekend_rate),
                        float(phase2_weekday_rate), float(phase2_weekend_rate),
                        reply_deadline,
                        target_spreadsheet_id,
                    )
                    detail = "、".join(f"{name} {count} 位" for name, count in result["areas"].items())
                    st.success(f"完成：{result['sheet']}，共 {result['count']} 位客戶（{detail}）")
                except Exception as exc:
                    st.exception(exc)

with nonroutine_tab:
    booking_start = st.date_input("開放預約日", value=date(2026, 11, 5))
    booking_end = st.date_input("預約截止日", value=date(2026, 11, 10))
    name = st.text_input("VIP 姓名／公司名稱")
    email = st.text_input("Email")
    line_url = st.text_input("LINE@ 客戶連結")
    preview_col, save_col = st.columns(2)
    preview_clicked = preview_col.button("預覽通知", use_container_width=True)
    save_clicked = save_col.button("加入 Email 合併清單", type="primary", use_container_width=True)
    if preview_clicked or save_clicked:
        if not name.strip():
            st.error("請填寫 VIP 姓名／公司名稱")
        elif save_clicked and not email.strip():
            st.error("加入 Email 合併清單前請填寫 Email")
        elif not rates_ready():
            st.error("請先填妥四項年節加價")
        else:
            notice = build_nonroutine_notice(
                name.strip(),
                dt(phase1_start), dt(phase1_end, True),
                dt(phase2_start), dt(phase2_end, True),
                float(phase1_weekday_rate), float(phase1_weekend_rate),
                float(phase2_weekday_rate), float(phase2_weekend_rate),
                dt(booking_start), dt(booking_end, True),
            )
            st.text_area("Email／LINE 通知內容", value=notice, height=520)
            st.text_input(
                "Email 主旨",
                value=f"【檸檬家事服務】{phase1_start.year}年節大掃除－VIP優先預約通知",
            )
            if email:
                st.caption(f"Email 收件人：{email}")
            if line_url.startswith(("http://", "https://")):
                st.link_button("開啟 LINE 對話", line_url, use_container_width=True)
            if save_clicked:
                try:
                    sheet_name = save_nonroutine_notice(
                        name, email, line_url, notice, phase1_start.year, target_spreadsheet_id
                    )
                    st.success(f"已加入「{sheet_name}」待寄清單")
                except Exception as exc:
                    st.exception(exc)
