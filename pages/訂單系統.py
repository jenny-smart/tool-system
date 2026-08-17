from __future__ import annotations

# ============================================================
# 檔名：pages/訂單系統.py
# 說明：整合 orders-system（訂單系統，內建已含備忘系統項目），
#       共用同一組「後台帳號/密碼/環境」，直接沿用 orders-system 原生的
#       分類功能選單（不再自行攤平維護一份清單）。
# ============================================================

import streamlit as st

from tools.orders_system.ui import render_orders_system
from utils.auth import authenticate
from utils.permissions import can_access_system

st.set_page_config(
    page_title="訂單系統",
    page_icon="🧹",
    layout="wide",
)

# ★ 側邊欄自動選單已在 .streamlit/config.toml 關閉，改用這個返回按鈕導覽。
st.page_link("toolapp.py", label="⬅ 返回 Tools App 主頁", icon="🏠")

# ------------------------------------------------------------------
# tool-system 登入 / 權限檢查
# ------------------------------------------------------------------


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

# ------------------------------------------------------------------
# 共用登入：後台帳號 / 密碼 / 環境（訂單系統、備忘系統共用同一組）
# ------------------------------------------------------------------

st.markdown("### 🔑 後台登入")
col_e, col_p, col_env = st.columns([3.2, 3.2, 1.2])
with col_e:
    shared_backend_email = st.text_input("後台帳號", key="shared_backend_email")
with col_p:
    shared_backend_password = st.text_input("後台密碼", type="password", key="shared_backend_password")
with col_env:
    shared_env = st.selectbox("環境", ["prod", "dev"], index=0, key="shared_env")

st.markdown("---")

# ------------------------------------------------------------------
# v2：不再維護獨立的攤平功能清單（FUNCTION_MAP）。
# orders-system 的「功能選單」本身就是完整分類清單（A~F，含每項說明，
# 也已經內建把備忘系統項目——排班管理／訂單客服備註／財務對帳／服務異動／
# 評估文字工具——路由到 tools/orders_system/memo_system 這個墊片，轉呼叫
# tool-system 真正的備忘系統）。這裡直接沿用它原生畫出來的下拉選單，
# 不再另外攤平或分流，日後 orders-system 新增／改名功能項目時，
# 這裡會自動一起更新，不用手動同步兩份清單。
# ------------------------------------------------------------------

render_orders_system(
    shared_backend_email=shared_backend_email,
    shared_backend_password=shared_backend_password,
    shared_env=shared_env,
)
