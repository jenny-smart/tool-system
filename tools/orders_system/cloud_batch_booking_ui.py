# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import json
import re
from datetime import datetime, timezone
from service_pricing import load_config
import requests
import streamlit as st
from accounts import ACCOUNTS
from batch_recovery_meta import install_patch as install_recovery_meta_patch
from selected_row_status_guard import install_patch as install_selected_row_status_guard
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
    try:
        previous = _latest_run()
    except Exception:
        previous = None
    requested_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    r = requests.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        headers={"Accept":"application/vnd.github+json", "Authorization":f"Bearer {token}", "X-GitHub-Api-Version":"2022-11-28"},
        json={"ref":"main", "inputs":{
            "sheet_name":sheet, "chunk_size":str(chunk_size), "max_rows":str(max_rows),
            "filter_mode":filter_mode, "region":region,
            "allow_auto_lemon":"true" if allow_auto_lemon else "false",
            "pricing_json": json.dumps(load_config()),
        }},
        timeout=30,
    )
    if r.status_code != 204:
        raise RuntimeError(f"啟動雲端批次失敗：{r.status_code} {r.text[:300]}")
    return {"previous_run_id": previous['id'] if previous else None, "requested_at": requested_at}



def _github_get(path):
    response = requests.get(
        f"https://api.github.com/repos/{REPO}/{path}",
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {_token()}",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=15,
    )
    response.raise_for_status()
    return response


def _latest_run():
    runs = _github_get(
        f"actions/workflows/{WORKFLOW}/runs?per_page=1&branch=main&event=workflow_dispatch"
    ).json().get("workflow_runs", [])
    return runs[0] if runs else None


def _run_summary(run_id):
    jobs = _github_get(f"actions/runs/{run_id}/jobs").json().get("jobs", [])
    for job in jobs:
        if job.get("name") == "booking":
            log = _github_get(f"actions/jobs/{job['id']}/logs").text
            matches = re.findall(r"FINISH attempted=(\d+) success=(\d+) fail=(\d+) remaining=(\d+)", log)
            if matches:
                return tuple(map(int, matches[-1]))
    return None


def _render_running_steps(run_id):
    jobs = _github_get(f"actions/runs/{run_id}/jobs").json().get("jobs", [])
    if not jobs:
        st.caption("正在等待雲端執行環境。")
        return
    labels = {
        "Set up job": "準備執行環境",
        "Run actions/checkout@v4": "準備成單程式",
        "Run actions/setup-python@v5": "準備執行環境",
        "Run pip install -r requirements.txt": "安裝執行所需套件",
        "Run optimized cloud batch booking": "批次成單、回填、寄確認信及同步日曆",
        "Complete job": "完成工作流程",
    }
    for job in jobs:
        active = [step for step in job.get("steps", []) if step.get("status") == "in_progress"]
        for step in active:
            name = step.get("name", "")
            st.write("目前步驟：" + labels.get(name, name))
        completed = sum(step.get("status") == "completed" for step in job.get("steps", []))
        total = len(job.get("steps", []))
        if total:
            st.caption(f"工作流程步驟：已結束 {completed}／{total} 個；此數字不是成單筆數。")
    st.caption("逐筆執行過程可點「查看日誌」；處理、成功、失敗及剩餘筆數於執行結束後顯示。")


@st.fragment(run_every="15s")
def _render_cloud_status():
    st.markdown("#### 最近一次雲端批次執行")
    st.markdown(f"[開啟雲端執行紀錄](https://github.com/{REPO}/actions/workflows/{WORKFLOW})")
    if not _token():
        st.warning("尚未設定 GitHub Token，頁面無法取得執行狀態；請開啟雲端執行紀錄查看。")
        return
    st.button("更新執行狀態", key="optimized_cloud_refresh")
    try:
        run = _latest_run()
        dispatch = st.session_state.get("optimized_cloud_dispatch")
        if dispatch:
            created_at = (run or {}).get("created_at", "")
            is_previous = run and run['id'] == dispatch.get('previous_run_id')
            is_older = bool(created_at and datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            < datetime.fromisoformat(dispatch['requested_at']))
            if not run or is_previous or is_older:
                st.info("啟動要求已送出，等待雲端建立本次執行紀錄；每 15 秒更新。")
                return
            del st.session_state['optimized_cloud_dispatch']
        if not run:
            st.info("尚無雲端批次執行紀錄。")
            return
        st.markdown(f"[第 {run['run_number']} 次執行・查看日誌]({run['html_url']})")
        if run.get("status") != "completed":
            if run.get("status") == "in_progress":
                st.info("雲端批次執行中；每 15 秒更新狀態。")
                try:
                    _render_running_steps(run['id'])
                except Exception:
                    st.caption("暫時無法取得目前步驟，請查看日誌；執行狀態仍會每 15 秒更新。")
            else:
                st.info("雲端批次排隊或準備中；每 15 秒更新狀態。")
            return
        conclusion = run.get("conclusion")
        cache_key = f"optimized_cloud_summary_{run['id']}_{run.get('run_attempt', 1)}"
        summary = st.session_state.get(cache_key)
        if summary is None:
            try:
                summary = _run_summary(run['id'])
                if summary is not None:
                    st.session_state[cache_key] = summary
            except Exception:
                st.caption("暫時無法取得筆數，請查看執行日誌。")
        if summary is not None:
            attempted, success, failed, remaining = summary
            message = f"已執行完成：處理 {attempted} 列，成功 {success} 列，失敗 {failed} 列，尚待處理 {remaining} 列。"
            if conclusion == "cancelled":
                st.warning("雲端批次已取消。" + message)
            elif failed:
                st.warning(message)
            elif conclusion == "success":
                st.success(message)
            else:
                st.error(message + " 工作流程仍有錯誤，請查看日誌。")
        elif conclusion == "success":
            st.success("雲端批次已執行完成。")
        elif conclusion == "cancelled":
            st.warning("雲端批次已取消。")
        else:
            st.error(f"雲端批次已結束（{conclusion}），請查看日誌確認原因。")
    except Exception:
        st.warning("暫時無法取得雲端狀態，請稍後更新或至 GitHub Actions 查看。")


def render(env: str):
    st.subheader("批次建單優化＋雲端批次成單")
    if env != "prod":
        st.warning("雲端批次成單只允許正式機 prod。")
        return

    status_panel = st.container()

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
    pending_key = (sheet, region, filter_mode, allow_auto_lemon)
    st.caption("N 欄結果為「失敗」且無單號的列會略過；清空結果可重試，勾選自動補檸檬人時也會列入處理。")

    if st.button("檢查待成單筆數", disabled=not sheet, key="optimized_cloud_check"):
        try:
            from cloud_batch_booking import load_pending
            rows = [row_no for row_no, _, _ in load_pending(
                sheet, filter_mode=filter_mode, selected_region=region, allow_auto_lemon=allow_auto_lemon,
            )]
            st.session_state.optimized_cloud_pending = len(rows)
            st.session_state.optimized_cloud_pending_rows = rows
            st.session_state.optimized_cloud_pending_key = pending_key
        except Exception as exc:
            st.error(f"讀取工作表失敗：{exc}")
    if st.session_state.get("optimized_cloud_pending_key") == pending_key:
        st.metric("目前符合所選條件", f"{st.session_state.optimized_cloud_pending} 列")
        rows = st.session_state.get("optimized_cloud_pending_rows") or []
        if rows:
            st.caption("列號：" + "、".join(map(str, rows[:100])) + ("…" if len(rows) > 100 else ""))

    confirm = st.checkbox("我確認執行：建單＋寄確認信＋同步 Google 日曆", key="optimized_cloud_confirm")
    if st.button("🚀  開始雲端批次成單", type="primary", use_container_width=True, disabled=not(sheet and confirm), key="optimized_cloud_start"):
        try:
            st.session_state['optimized_cloud_dispatch'] = _dispatch(sheet, int(chunk), int(max_rows), filter_mode, region, allow_auto_lemon)
            st.success("已交給 GitHub Actions 雲端執行；可關閉瀏覽器或電腦。")
            st.markdown(f"[查看雲端執行進度](https://github.com/{REPO}/actions/workflows/{WORKFLOW})")
        except Exception as exc:
            st.error(str(exc))

    with status_panel:
        _render_cloud_status()
