from __future__ import annotations

# ============================================================
# tools/orders_system/ui.py
#
# ordersapp.py 是從 orders-system 同步來的舊式 Streamlit 頁面，會在 import
# 時直接畫 UI，且目前沒有 render_orders_page()。這裡提供 tool-system 需要的
# render_orders_system() 包裝，避免 pages/訂單系統.py 匯入時直接爆掉。
# ============================================================

import runpy
from collections.abc import Sequence
from typing import Any

import streamlit as st


def _pick_forced_option(options: Sequence[Any], forced_mode: str) -> Any:
    for option in options:
        if forced_mode and forced_mode in str(option):
            return option
    return options[0] if options else None


def render_orders_system(
    *,
    forced_mode: str | None = None,
    shared_backend_email: str = "",
    shared_backend_password: str = "",
    shared_env: str = "prod",
) -> None:
    original_set_page_config = st.set_page_config
    original_text_input = st.text_input
    original_selectbox = st.selectbox

    def patched_set_page_config(*args: Any, **kwargs: Any) -> None:
        return None

    def patched_text_input(label: str, *args: Any, **kwargs: Any) -> Any:
        if label == "後台帳號":
            return shared_backend_email
        if label == "後台密碼":
            return shared_backend_password
        return original_text_input(label, *args, **kwargs)

    def patched_selectbox(label: str, options: Sequence[Any], *args: Any, **kwargs: Any) -> Any:
        if label == "環境":
            return "dev（測試機 backend-dev）" if shared_env == "dev" else "prod（正式機 backend）"
        if label == "功能選單" and forced_mode:
            return _pick_forced_option(options, forced_mode)
        return original_selectbox(label, options, *args, **kwargs)

    st.set_page_config = patched_set_page_config
    st.text_input = patched_text_input
    st.selectbox = patched_selectbox
    try:
        runpy.run_module("tools.orders_system.ordersapp", run_name="__main__")
    finally:
        st.set_page_config = original_set_page_config
        st.text_input = original_text_input
        st.selectbox = original_selectbox


__all__ = ["render_orders_system"]
