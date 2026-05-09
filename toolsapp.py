import streamlit as st
from tools.vip_stored_value import render as render_vip_stored_value

st.set_page_config(page_title="Jenny Tools App", layout="wide")

st.sidebar.title("Jenny Tools App")
tool = st.sidebar.radio("工具", ["儲值金管理"])

if tool == "儲值金管理":
    render_vip_stored_value()
