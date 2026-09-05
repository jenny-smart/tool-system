# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import requests
import streamlit as st
import batch_booking_optimized as batch_opt
from accounts import ACCOUNTS
from batch_recovery_meta import install_patch as install_recovery_meta_patch
from selected_row_status_guard import install_patch as install_selected_row_status_guard, _auto_filter_rows
from batch_booking_safety import install_streamlit_batch_hooks

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


def _dispatch(sheet, chunk_size, max_rows, filter_mode, region, allow_auto_lemon):
    token = _token()
    if not token:
        raise RuntimeError("尚未設定 ORDERS_GITHUB_TOKEN；需提供可啟動 orders-system Actions 的 GitHub Token。")
    r = requests.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        headers={"Accept":"application/vnd.github+json", "Authorization":f"Bearer {token}", "X-GitHub-Api-Version":"2022-11-28"},
        json={"ref":"main", "inputs":{
            "sheet_name":sheet, "chunk_size":str(chunk_size), "max_rows":str(max_rows),
            "filter_mode":filter_mode, "region":region,
            "allow_auto_lemon":"true" if allow_auto_lemon else "false",
        }},
        timeout=30,
    )
    if r.status_code != 204:
        raise RuntimeError(f"啟動雲端批次失敗：{r.status_code} {r.text[:300]}")


def render(env: str):
    st.subheader("批次建單優化＋雲端批次成單")
    if env != "prod":
        st.warning("雲端批次成單只允許正式機 prod。")
        return

    st.markdown("#### 4　執行設定")
    c1, c2, c3 = st.columns(3)
    regions = list(ACCOUNTS.keys()) or ["台北"]
    default_index = regions.index("台北") if "台北" in regions else 0
    region = c1.selectbox("執行區域", regions, index=default_index, key="optimized_cloud_region")
    sheet = c2.text_input("工作表名稱", placeholder="例：台北202610", key="optimized_cloud_sheet").strip()
    c3.text_input("執行列號", value="雲端依下方篩選自動取得", disabled=True, key="optimized_cloud_rows_hint")
    st.info("💡 雲端版由篩選條件自動取得列號；中斷重跑會先反查後台，避免重複建單。")

    st.markdown("#### 3　執行項目")
    st.multiselect(
        "執行項目", ["建單", "寄確認信", "改 Google 日曆"],
        default=["建單", "寄確認信", "改 Google 日曆"], disabled=True,
        key="optimized_cloud_actions", label_visibility="collapsed",
    )
    allow_auto_lemon = st.checkbox(
        "查無班表時自動補檸檬人（不動其他客人已配班專員）",
        value=False, key="optimized_cloud_allow_auto_lemon",
    )
    auto_no_slot = st.checkbox(
        "自動篩選：狀態未安排＋訂單編號空白＋無班表",
        value=False, key="optimized_cloud_no_schedule",
    )
    auto_missing_o = st.checkbox(
        "自動篩選：狀態未安排＋訂單編號空白＋O欄找不到訂單編號",
        value=False, key="optimized_cloud_missing_o",
    )
    c1, c2 = st.columns(2)
    chunk = c1.number_input("每輪最多處理列數", 1, 200, 50, 10, key="optimized_cloud_chunk")
    max_rows = c2.number_input("本次最多處理列數（0＝全部）", 0, 5000, 0, 10, key="optimized_cloud_max")

    modes = []
    if auto_no_slot:
        modes.append("no_schedule")
    if auto_missing_o:
        modes.append("missing_order")
    filter_mode = "both" if len(modes) == 2 else (modes[0] if modes else "all")

    if st.button("檢查待成單筆數", disabled=not sheet, key="optimized_cloud_check"):
        try:
            if filter_mode == "all":
                rows = [
                    int(r["__sheet_row__"]) for _, r in batch_opt._load_candidates(sheet).iterrows()
                    if not batch_opt._text(r.get("訂單編號"))
                    and batch_opt.get_region_by_address(batch_opt._text(r.get("地址")), ACCOUNTS) == region
                ]
            else:
                rows = set()
                if filter_mode in ("no_schedule", "both"):
                    rows.update(_auto_filter_rows(batch_opt, sheet, "no_schedule", region=region))
                if filter_mode in ("missing_order", "both"):
                    rows.update(_auto_filter_rows(batch_opt, sheet, "missing_order", region=region))
                rows = sorted(rows)
            st.session_state.optimized_cloud_pending = len(rows)
            st.session_state.optimized_cloud_pending_rows = rows
        except Exception as exc:
            st.error(f"讀取工作表失敗：{exc}")
    if "optimized_cloud_pending" in st.session_state:
        st.metric("目前符合所選條件", f"{st.session_state.optimized_cloud_pending} 列")
        rows = st.session_state.get("optimized_cloud_pending_rows") or []
        if rows:
            st.caption("列號：" + "、".join(map(str, rows[:100])) + ("…" if len(rows) > 100 else ""))

    confirm = st.checkbox("我確認執行：建單＋寄確認信＋同步 Google 日曆", key="optimized_cloud_confirm")
    if st.button("🚀  開始雲端批次成單", type="primary", use_container_width=True, disabled=not(sheet and confirm), key="optimized_cloud_start"):
        try:
            _dispatch(sheet, int(chunk), int(max_rows), filter_mode, region, allow_auto_lemon)
            st.success("已交給 GitHub Actions 雲端執行；可關閉瀏覽器或電腦。")
            st.markdown(f"[查看雲端執行進度](https://github.com/{REPO}/actions/workflows/{WORKFLOW})")
        except Exception as exc:
            st.error(str(exc))
