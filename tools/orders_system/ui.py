from __future__ import annotations

# ============================================================
# tools/orders_system/ui.py
#
# ordersapp.py 是從 orders-system 同步來的舊式 Streamlit 頁面，會在 import
# 時直接畫 UI，且目前沒有 render_orders_page()。這裡提供 tool-system 需要的
# render_orders_system() 包裝，避免呼叫端匯入時直接爆掉。
#
# 後台帳號/密碼/環境、功能選單都直接沿用 ordersapp.py 原生畫出來的欄位，
# 不再由呼叫端重複收集一次再轉塞進去——原本的轉塞邏輯是給「訂單/備忘
# 系統各自獨立渲染、需要共用登入」的舊架構用的，現在備忘系統的功能已經
# 由 ordersapp.py 自己在內部路由過去（見 tools/orders_system/memo_system
# 這個墊片），只剩單一入口，不需要再轉塞。
# ============================================================

import runpy
from typing import Any

import streamlit as st


def render_orders_system() -> None:
    original_set_page_config = st.set_page_config

    def patched_set_page_config(*args: Any, **kwargs: Any) -> None:
        return None

    st.set_page_config = patched_set_page_config
    try:
        runpy.run_module("tools.orders_system.ordersapp", run_name="__main__")
    finally:
        st.set_page_config = original_set_page_config


__all__ = ["render_orders_system"]
