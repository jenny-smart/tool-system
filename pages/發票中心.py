from __future__ import annotations

import streamlit as st

from tools.invoice_center.ui import render_invoice_center
from utils.auth import authenticate
from utils.permissions import can_access_system


st.set_page_config(
    page_title="發票中心",
    page_icon="🧾",
    layout="wide",
)


def _render_login_page() -> None:
    st.markdown("## 🔐 發票中心登入")
    st.caption("請使用 Tools App 系統帳號登入後操作。")
    with st.form("invoice_center_login_form"):
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

if not can_access_system("invoice_center"):
    st.error("你沒有權限使用發票中心")
    st.stop()

render_invoice_center()
