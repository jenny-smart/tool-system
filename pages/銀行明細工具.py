from __future__ import annotations

from pathlib import Path

import streamlit as st

from utils.auth import authenticate
from utils.permissions import can_access_system


st.set_page_config(
    page_title="銀行明細工具",
    page_icon="🏦",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = BASE_DIR / "tools" / "bank_statement" / "fubon_statement_copy.user.js"


def _render_login_page() -> None:
    st.markdown("## 🔐 銀行明細工具登入")
    st.caption("請使用 Tools App 系統帳號登入後操作。")
    with st.form("bank_statement_login_form"):
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

if not can_access_system("bank_statement_tool"):
    st.error("你沒有權限使用銀行明細工具")
    st.stop()

st.page_link("toolapp.py", label="⬅ 返回 Tools App 主頁", icon="🏠")
st.title("🏦 銀行明細複製工具")
st.caption("在台北富邦銀行交易明細頁直接複製資料，再貼到 Google Sheet；不下載銀行明細檔。")

st.warning("銀行帳號、密碼與驗證碼不會也不應寫入 Streamlit Secret。請自行正常登入銀行。")

script_bytes = SCRIPT_PATH.read_bytes()
st.download_button(
    "下載／更新 Tampermonkey 腳本",
    data=script_bytes,
    file_name=SCRIPT_PATH.name,
    mime="application/javascript",
    use_container_width=True,
)

st.markdown(
    """
### 第一次安裝

1. Chrome 安裝 Tampermonkey。
2. 按上方按鈕取得腳本，以 Tampermonkey 開啟並安裝。
3. 自行登入台北富邦網銀，進入「臺外幣交易明細查詢」。

### 日常操作

- **複製新增明細**：只複製這個 Chrome 尚未成功複製過的交易，接著到 Google Sheet 的 B 欄第一個空白格貼上。
- **調整交易列數**：獨立開啟清單，為各筆新交易設定 1～20 列後再複製；重複列不重複填入收入／支出金額。
- **重複上一批**：貼上失敗時使用，不會改變新增資料判斷。
- **重設今日紀錄**：讓今天畫面中的交易重新被視為新增資料。

已複製判斷只保存在目前的 Chrome／Tampermonkey，不會傳送銀行資料到 Tools App。
"""
)
