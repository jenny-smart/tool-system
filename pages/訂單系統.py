from __future__ import annotations

# ============================================================
# 檔名：pages/訂單系統.py
# 說明：整合 orders-system（訂單系統）與 memo-system（備忘系統），
#       共用同一組「後台帳號/密碼/環境」，並攤平成單一功能選單。
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
# 攤平後的單一功能選單
# ------------------------------------------------------------------

FUNCTION_MAP = {
    "批次建單": ("orders", "批次建單（Google Sheet）"),
    "舊客建單": ("orders", "舊客快速建單"),
    "新客建單": ("orders", "新客資料拆解"),
    "儲值金建單": ("orders", "儲值金購買"),
    "訂單轉換": ("orders", "訂單轉換"),
    "儲值金補價差": ("orders", "儲值金補價差"),
    "排班管理": ("memo", "📅 排班管理"),
    "LINE通知訊息": ("orders", "LINE 通知產生器"),
    "訂單備註": ("memo", "📋 客服作業"),
    "對帳管理": ("memo", "💰 財務對帳"),
    "異動管理": ("memo", "🔄 服務異動"),
    "評估工具": ("memo", "📐 評估文字工具"),
    "雙向訂單檢查": ("orders", "雙向訂單檢查"),
    "查詢無LINE連結訂單": ("orders", "查詢無LINE連結訂單"),
    "儲值獎金備註": ("orders", "儲值獎金備註"),
}

function_choice = st.selectbox(
    "選擇功能",
    ["請選擇"] + list(FUNCTION_MAP.keys()),
    label_visibility="collapsed",
    key="orders_memo_function_choice",
)

if function_choice != "請選擇":
    system_key, internal_mode = FUNCTION_MAP[function_choice]

    if system_key == "orders":
        render_orders_system(
            forced_mode=internal_mode,
            shared_backend_email=shared_backend_email,
            shared_backend_password=shared_backend_password,
            shared_env=shared_env,
        )
    else:
        render_memo_system(
            forced_main_section=internal_mode,
            shared_backend_email=shared_backend_email,
            shared_backend_password=shared_backend_password,
            shared_env=shared_env,
        )
