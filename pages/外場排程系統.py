from __future__ import annotations

# ============================================================
# 檔名：pages/外場排程系統.py
# 說明：外場排程系統的手動操作工具──新增名冊及調薪、履歷系統。
#       原本是「新增名冊及調薪.gs」「履歷系統.gs」兩支 Google Apps Script
#       的選單功能，這裡整合進 tool-system，改用 Google Sheets API / IMAP
#       存取資料（詳見 tools/field_management/roster_raise.py 與
#       tools/field_management/resume_system.py）。
# ============================================================

import streamlit as st

from tools.field_management.ui import render_resume_system, render_roster_raise_system
from utils.auth import authenticate
from utils.permissions import can_access_system

st.set_page_config(
    page_title="外場排程系統｜名冊 / 調薪 / 履歷",
    page_icon="📋",
    layout="wide",
)

# ★ 側邊欄自動選單已在 .streamlit/config.toml 關閉，改用這個返回按鈕導覽。
st.page_link("toolapp.py", label="⬅ 返回 Tools App 主頁", icon="🏠")

# ------------------------------------------------------------------
# tool-system 登入 / 權限檢查
# ------------------------------------------------------------------


def _render_login_page() -> None:
    st.markdown("## 🔐 外場排程系統登入")
    st.caption("請使用 Tools App 系統帳號登入後操作。")
    with st.form("field_management_extra_login_form"):
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入", use_container_width=True)
    if submitted:
        user = authenticate(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user["username"]
            st.session_state.role = user["role"]
            st.rerun()
        st.error("帳號或密碼錯誤")


if not st.session_state.get("logged_in"):
    _render_login_page()
    st.stop()

if not can_access_system("field_daily_schedule"):
    st.error("你沒有權限使用外場排程系統")
    st.stop()

# ------------------------------------------------------------------
# 功能選單
# ------------------------------------------------------------------

st.markdown(
    '<div class="app-title">📋 外場排程系統｜名冊 / 調薪 / 履歷</div>',
    unsafe_allow_html=True,
)

function_choice = st.radio(
    "選擇功能",
    ["新增名冊及調薪", "履歷系統"],
    horizontal=True,
    label_visibility="collapsed",
    key="field_extra_function_choice",
)

st.markdown("---")

if function_choice == "新增名冊及調薪":
    render_roster_raise_system()
else:
    render_resume_system()
