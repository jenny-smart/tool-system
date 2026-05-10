from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="業績報表",
    page_icon="📊",
    layout="wide",
)

BASE_DIR = Path("dashboard_data")
LATEST_DIR = BASE_DIR / "latest"

DF4_PATH = LATEST_DIR / "df4.csv"
DAILY_PATH = LATEST_DIR / "daily_df.csv"
NEXT_MONTH_PATH = LATEST_DIR / "next_month_daily_df.csv"
META_PATH = LATEST_DIR / "meta.json"
EMAIL_HTML_PATH = LATEST_DIR / "email_preview.html"


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()

    return pd.read_csv(path, encoding="utf-8-sig")


def load_meta() -> dict:
    if not META_PATH.exists():
        return {}

    with META_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


st.title("📊 業績報表")

meta = load_meta()

if meta:
    st.caption(f"最後更新：{meta.get('updated_at', '-')}")

    if meta.get("error"):
        st.warning(meta.get("error"))

df4 = load_csv(DF4_PATH)
daily_df = load_csv(DAILY_PATH)
next_month_df = load_csv(NEXT_MONTH_PATH)

if df4.empty:
    st.info("尚未產生業績報表資料")
    st.stop()

st.subheader("各區業績總覽")
st.dataframe(df4, use_container_width=True)

st.divider()

c1, c2 = st.columns(2)

with c1:
    st.subheader("本月每日追蹤")

    if daily_df.empty:
        st.info("尚無資料")
    else:
        st.dataframe(daily_df, use_container_width=True)

with c2:
    st.subheader("次月每日追蹤")

    if next_month_df.empty:
        st.info("尚無資料")
    else:
        st.dataframe(next_month_df, use_container_width=True)

st.divider()

st.subheader("Email 預覽")

if EMAIL_HTML_PATH.exists():
    html = EMAIL_HTML_PATH.read_text(encoding="utf-8")
    st.components.v1.html(html, height=500, scrolling=True)
else:
    st.info("尚無 Email HTML")
