"""整合執行共用 UI：盤點結果表格上方的「全選／全不選」。

st.data_editor 的勾選狀態存在元件自己的 state，要整欄改掉只能換 key 重建；
所以按鈕記下「全部勾／全部不勾」並遞增版本號，呼叫端用回傳的 key 重建表格。
"""
from __future__ import annotations

from typing import Optional

import streamlit as st


def select_all_controls(prefix: str) -> tuple[Optional[bool], str]:
    """畫「全選／全不選」兩顆按鈕。回傳 (覆寫值, data_editor key)：
    覆寫值 None＝照盤點預設（目前可做的才打勾）；True／False＝全部勾／全部不勾。"""
    flag_key, version_key = f"{prefix}_select_all", f"{prefix}_select_version"
    col_all, col_none, _ = st.columns([1, 1, 6])
    if col_all.button("☑️ 全選", key=f"{prefix}_select_all_btn"):
        st.session_state[flag_key] = True
        st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
    if col_none.button("⬜ 全不選", key=f"{prefix}_select_none_btn"):
        st.session_state[flag_key] = False
        st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
    return st.session_state.get(flag_key), f"{prefix}_editor_{st.session_state.get(version_key, 0)}"


def reset_select_all(prefix: str) -> None:
    """重新盤點後回到預設勾選（目前可做的才打勾）。"""
    st.session_state.pop(f"{prefix}_select_all", None)
    st.session_state[f"{prefix}_select_version"] = st.session_state.get(f"{prefix}_select_version", 0) + 1


def checked(default: bool, override: Optional[bool], selectable: bool = True) -> bool:
    if not selectable:
        return False
    return default if override is None else override
