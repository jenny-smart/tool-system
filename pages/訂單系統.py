from __future__ import annotations

# ============================================================
# 檔名：pages/訂單系統.py
# 說明：整合原本 orders-system（訂單系統）與 memo-system（備忘系統）
#       兩個獨立 repo，合併成 tool-system 底下的一個分頁。
#
# 設計重點（依需求）：
# 1. 這一頁只負責「tool-system 本身的登入/權限檢查」+「選擇要用訂單系統
#    還是備忘系統」。選了之後才呼叫對應的 render 函式，兩邊各自原本的
#    「後台帳號密碼 + 正式機/測試機」登入方式完全不動，維持原樣。
# 2. 選擇系統之前（st.radio 預設停在「請選擇」），畫面上不會出現任何一邊
#    系統的內容，只有一個選擇列。
# 3. orders-system / memo-system 兩個獨立 repo 先保留當備份，這裡是把
#    程式碼複製過來整合，不是搬移，原本兩個 repo 還能照常單獨使用/部署。
# ============================================================

import streamlit as st

from tools.orders_system.ui import render_orders_system
from tools.memo_system.ui import render_memo_system
from utils.auth import authenticate
from utils.permissions import can_access_system

st.set_page_config(
    page_title="訂單系統",
    page_icon="🧹",
    layout="wide",
)


def _render_login_page() -> None:
    st.markdown("## 🔐 訂單系統登入")
    st.caption("請使用 Tools App 系統帳號登入後操作。")
    with st.form("orders_memo_login_form"):
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

if not can_access_system("orders_memo_system"):
    st.error("你沒有權限使用訂單系統")
    st.stop()

# --------------------------------------------------
# 選擇要用「訂單系統」還是「備忘系統」
# 預設停在「請選擇」，選之前下面不會出現任何一邊系統的內容。
# --------------------------------------------------
system_choice = st.radio(
    "選擇系統",
    ["請選擇", "📦 訂單系統", "🍋 備忘系統"],
    horizontal=True,
    label_visibility="collapsed",
    key="orders_memo_system_choice",
)

if system_choice == "📦 訂單系統":
    render_orders_system()
elif system_choice == "🍋 備忘系統":
    render_memo_system()
