from __future__ import annotations

import re
import streamlit as st

from services.google_auth import get_drive_service, get_gspread_client, get_sheets_service
from services.google_drive import DriveService
from services.google_sheets import SheetsService
from services.vip_workflow import VipStoredValueWorkflow


def valid_period(period: str) -> bool:
    return bool(re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period or ""))


def get_workflow() -> VipStoredValueWorkflow:
    master_id = st.secrets.get("MASTER_SPREADSHEET_ID", "")
    root_folder_id = st.secrets.get("ROOT_FOLDER_ID", "")
    if not master_id or not root_folder_id:
        st.error("請先在 .streamlit/secrets.toml 設定 MASTER_SPREADSHEET_ID 與 ROOT_FOLDER_ID")
        st.stop()
    drive = DriveService(get_drive_service())
    sheets = SheetsService(get_gspread_client(), get_sheets_service())
    return VipStoredValueWorkflow(drive, sheets, master_id, root_folder_id)


def show_results(results):
    for res in results if isinstance(results, list) else [results]:
        with st.expander(f"{res.step}結果", expanded=True):
            for msg in res.messages:
                st.success(msg)
            for err in res.errors:
                st.error(err)


def render():
    st.header("儲值金管理")
    st.caption("主控表驅動：建立每月彙整檔、轉檔、搬運、套公式、彙整金額。")

    period = st.text_input("期別 yyyyMM", placeholder="例如 202604")
    if period and not valid_period(period):
        st.warning("期別格式請輸入 yyyyMM，例如 202604")
        return

    col1, col2, col3 = st.columns(3)
    col4, col5, col6 = st.columns(3)

    if not period:
        st.info("請先輸入期別。")
        return

    workflow = None
    def wf():
        nonlocal workflow
        if workflow is None:
            workflow = get_workflow()
        return workflow

    with col1:
        if st.button("建立當月彙整檔", use_container_width=True):
            show_results(wf().create_monthly_summary(period))
    with col2:
        if st.button("轉檔＋高雄/新竹整理", use_container_width=True):
            show_results(wf().convert_files(period))
    with col3:
        if st.button("搬運資料", use_container_width=True):
            show_results(wf().move_data(period))
    with col4:
        if st.button("套用公式 / 計算", use_container_width=True):
            show_results(wf().apply_formulas(period))
    with col5:
        if st.button("彙整金額", use_container_width=True):
            show_results(wf().summarize_amounts(period))
    with col6:
        if st.button("完整一鍵執行", type="primary", use_container_width=True):
            show_results(wf().run_all(period))

    st.divider()
    st.subheader("高雄/新竹整理規則")
    st.write("新竹檔：移除 H 欄為 `儲值金18900`、`儲值金36000`、`儲值金9900` 的列；高雄檔：只保留 H 欄為這三種儲值金的列，其餘列移除。")

    st.subheader("打卡順序")
    st.write("固定依序執行：轉檔 → 搬運 → 計算 → 彙整金額。每個項目會寫回主控表的 `月度作業紀錄`，每月一欄。")
