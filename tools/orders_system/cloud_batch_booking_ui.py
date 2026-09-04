# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import requests
import streamlit as st
from batch_booking_optimized import _load_candidates
from batch_recovery_meta import install_patch as install_recovery_meta_patch
from selected_row_status_guard import install_patch as install_selected_row_status_guard
from batch_booking_safety import install_streamlit_batch_hooks

# 先安裝指定列狀態防呆與中斷復原，再接上三個批次入口。
# 指定列只有「未安排＋訂單編號空白」可建單；待確認／已安排／暫停／保留單不執行。
install_selected_row_status_guard()
install_recovery_meta_patch()
install_streamlit_batch_hooks()

REPO = "jenny-smart/orders-system"
WORKFLOW = "optimized-cloud-batch-booking.yml"


def _token():
    for key in ("ORDERS_GITHUB_TOKEN", "GITHUB_ACTIONS_TOKEN", "GH_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
        try:
            value = str(st.secrets.get(key, "")).strip()
            if value:
                return value
        except Exception:
            pass
    return ""


def _dispatch(sheet, chunk_size, max_rows):
    token = _token()
    if not token:
        raise RuntimeError("尚未設定 ORDERS_GITHUB_TOKEN；需提供可啟動 orders-system Actions 的 GitHub Token。")
    r = requests.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        headers={"Accept":"application/vnd.github+json", "Authorization":f"Bearer {token}", "X-GitHub-Api-Version":"2022-11-28"},
        json={"ref":"main", "inputs":{"sheet_name":sheet, "chunk_size":str(chunk_size), "max_rows":str(max_rows)}},
        timeout=30,
    )
    if r.status_code != 204:
        raise RuntimeError(f"啟動雲端批次失敗：{r.status_code} {r.text[:300]}")


def render(env: str):
    st.subheader("批次建單優化＋雲端批次成單")
    st.info("這是獨立雲端功能；原『批次建單優化』完全保留給人工成單。本功能沿用相同候選篩選與批次建單核心，啟動後由 GitHub Actions 執行，關閉電腦不會中斷。中斷後重跑會先反查後台已成立訂單，找到後會補回訂單編號與後台訂單資料，不重複建單。")
    if env != "prod":
        st.warning("雲端批次成單只允許正式機 prod。")
        return
    sheet = st.text_input("工作表名稱", placeholder="例如：台北202609", key="optimized_cloud_sheet").strip()
    c1, c2 = st.columns(2)
    chunk = c1.number_input("每輪最多處理列數", 1, 200, 50, 10, key="optimized_cloud_chunk")
    max_rows = c2.number_input("本次最多處理列數（0＝全部）", 0, 5000, 0, 10, key="optimized_cloud_max")
    if st.button("檢查待成單筆數", disabled=not sheet, key="optimized_cloud_check"):
        try:
            st.session_state.optimized_cloud_pending = len(_load_candidates(sheet))
        except Exception as exc:
            st.error(f"讀取工作表失敗：{exc}")
    if "optimized_cloud_pending" in st.session_state:
        st.metric("目前符合批次建單優化條件", f"{st.session_state.optimized_cloud_pending} 列")
    confirm = st.checkbox("我確認執行：建單＋寄確認信＋同步 Google 日曆", key="optimized_cloud_confirm")
    if st.button("開始雲端批次成單", type="primary", disabled=not(sheet and confirm), key="optimized_cloud_start"):
        try:
            _dispatch(sheet, int(chunk), int(max_rows))
            st.success("已交給 GitHub Actions 雲端執行；可關閉瀏覽器或電腦。")
            st.markdown(f"[查看雲端執行進度](https://github.com/{REPO}/actions/workflows/{WORKFLOW})")
        except Exception as exc:
            st.error(str(exc))
