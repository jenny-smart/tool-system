from __future__ import annotations

# ============================================================
# tools/field_management/ui.py
#
# 外場排程系統手動操作工具的 Streamlit 介面：
#   - 新增名冊及調薪（roster_raise.py）
#   - 履歷系統（resume_system.py）
#
# 對應原本 GAS 選單裡用 ui.prompt() 詢問使用者的月份 / 時間區間等輸入，
# 這裡改用 Streamlit 表單元件呈現。
# ============================================================

import re
from datetime import datetime, timedelta, timezone

import streamlit as st

from tools.field_management import resume_system, roster_raise
from tools.field_management.staff_profile import area_list_from_config, load_system_config

TZ = timezone(timedelta(hours=8))
SYSTEM_NAME = "外場排程系統"

_YM_PATTERN = re.compile(r"\d{6}")


def _areas() -> list[str]:
    try:
        cfg = load_system_config(SYSTEM_NAME)
        return area_list_from_config(cfg)
    except Exception:
        return ["台北", "台中"]


def _is_valid_ym(ym: str) -> bool:
    return bool(_YM_PATTERN.fullmatch(ym.strip()))


def _show_mail_diagnostics(result: dict) -> None:
    """顯示這次連線的信箱帳號、IMAP 搜尋條件與命中封數。
    新增 0 筆時最常見的原因是接錯信箱（RESUME_GMAIL_USER 沒設定，退回用
    GMAIL_USER 連到別的帳號）——這裡直接把命中封數跟帳號顯示出來，
    不用另外去看伺服器 log 才能判斷是「真的沒有信」還是「連錯帳號」。"""
    mailbox = result.get("mailbox")
    if not mailbox:
        return
    with st.expander("連線與搜尋明細", expanded=result.get("matched", 0) == 0 and result.get("count", 0) == 0):
        st.caption(f"信箱：{mailbox}")
        st.caption(f"IMAP 命中：{result.get('matched', '-')} 封")
        st.code(result.get("criteria", ""), language="text")


# ────────────────────────────────────────────────────────────
# 新增名冊及調薪
# ────────────────────────────────────────────────────────────

def render_roster_raise_system() -> None:
    st.subheader("📋 新增名冊及調薪")

    areas = _areas()
    tab_roster, tab_raise, tab_convert = st.tabs(["新增專員名冊", "新增調薪資料", "調薪工作表轉為值"])

    with tab_roster:
        st.caption("複製上期「YYYYMM專員名冊」為新期別，並把離職人員（F欄有值）移到「離職名冊」。")
        area = st.selectbox("地區", areas, key="roster_area")
        ym = st.text_input("新增月份（格式：202604）", key="roster_ym")

        if st.button("建立專員名冊", key="btn_create_roster", type="primary"):
            if not _is_valid_ym(ym):
                st.error("❗ 月份格式錯誤，請輸入 6 碼，例如：202604")
            else:
                try:
                    with st.spinner("建立中..."):
                        result = roster_raise.create_roster_sheet(area, ym.strip())
                    st.success(
                        f"✅ 專員名冊完成｜{result['source']} → {result['new']}"
                        f"｜離職名冊已新增 {result['moved']} 筆"
                    )
                except Exception as exc:
                    st.error(f"❌ 新增專員名冊失敗：{exc}")

    with tab_raise:
        st.caption("複製上期「YYYYMM調薪資料」為新期別，重建 B3 IMPORTRANGE、K 欄、N 欄公式。")
        area = st.selectbox("地區", areas, key="raise_area")
        ym = st.text_input("調薪月份（格式：202604）", key="raise_ym")

        if st.button("建立調薪資料", key="btn_create_raise", type="primary"):
            if not _is_valid_ym(ym):
                st.error("❗ 月份格式錯誤，請輸入 6 碼，例如：202604")
            else:
                try:
                    with st.spinner("建立中..."):
                        result = roster_raise.create_raise_sheet(area, ym.strip())
                    st.success(f"✅ 調薪資料完成｜{result['source']} → {result['new']}")
                except Exception as exc:
                    st.error(f"❌ 新增調薪資料失敗：{exc}")

    with tab_convert:
        st.caption("把「YYYYMM調薪資料」A3:AE 範圍的公式轉為固定值（結算後鎖定用）。")
        area = st.selectbox("地區", areas, key="convert_area")
        ym = st.text_input("期別（格式：202603）", key="convert_ym")

        if st.button("轉為值", key="btn_convert_raise", type="primary"):
            if not _is_valid_ym(ym):
                st.error("❗ 期別格式錯誤，請輸入 6 碼，例如：202603")
            else:
                try:
                    with st.spinner("轉換中..."):
                        result = roster_raise.convert_raise_sheet_to_values(area, ym.strip())
                    if result["converted_rows"]:
                        st.success(f"✅ 已完成轉為值｜{result['sheet']}｜共 {result['converted_rows']} 列")
                    else:
                        st.warning(f"⚠️ {result['sheet']} 的 A3 以下沒有可轉為值的資料")
                except Exception as exc:
                    st.error(f"❌ 調薪工作表轉為值失敗：{exc}")


# ────────────────────────────────────────────────────────────
# 履歷系統
# ────────────────────────────────────────────────────────────

def render_resume_system() -> None:
    st.subheader("📄 履歷系統")
    st.caption(
        "使用 IMAP 讀取 Gmail（需先設定 RESUME_GMAIL_USER / RESUME_GMAIL_APP_PASSWORD，"
        "未設定則沿用 GMAIL_USER / GMAIL_APP_PASSWORD）。"
    )

    areas = _areas()
    tab_range, tab_replies, tab_latest = st.tabs(["擷取 104履歷", "擷取 104回覆信", "擷取最新雙北履歷"])

    with tab_range:
        st.caption("依起始/結束時間，從 Gmail 擷取「104應徵履歷」信件並寫入「104應徵履歷」工作表。")
        area = st.selectbox("地區（決定寫入哪份名冊試算表）", areas, key="resume_range_area")

        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("起始日期", key="resume_start_date")
            start_time = st.time_input("起始時間", key="resume_start_time")
        with col2:
            end_date = st.date_input("結束日期", value=datetime.now(TZ).date(), key="resume_end_date")
            end_time = st.time_input("結束時間", value=datetime.now(TZ).time(), key="resume_end_time")

        if st.button("開始擷取", key="btn_fetch_range", type="primary"):
            start_dt = datetime.combine(start_date, start_time).replace(tzinfo=TZ)
            end_dt = datetime.combine(end_date, end_time).replace(tzinfo=TZ)

            if start_dt > end_dt:
                st.error("❗ 起始時間不能晚於結束時間")
            else:
                try:
                    with st.spinner("擷取中..."):
                        result = resume_system.fetch_resumes_range(area, start_dt, end_dt)
                    st.success(f"✅ 完成擷取，共新增 {result['count']} 筆")
                    _show_mail_diagnostics(result)
                except Exception as exc:
                    st.error(f"❌ 擷取失敗：{exc}")

    with tab_replies:
        st.caption("擷取 LEMON HOME 回覆信，寫入「104回覆紀錄」工作表，並在 Gmail 標記「已登記」。")
        area = st.selectbox("地區（決定寫入哪份名冊試算表）", areas, key="resume_reply_area")

        if st.button("開始擷取回覆信", key="btn_fetch_replies", type="primary"):
            try:
                with st.spinner("擷取中..."):
                    result = resume_system.fetch_lemon_home_replies(area)
                st.success(f"✅ 完成擷取，共新增 {result['count']} 筆")
                _show_mail_diagnostics(result)
            except Exception as exc:
                st.error(f"❌ 擷取 104 回覆信失敗：{exc}")

    with tab_latest:
        st.caption(
            "掃描未讀且主旨含雙北地區關鍵字的「104應徵履歷」信件，過濾內勤/助理等職缺後寫入指定工作表，"
            "並在 Gmail 標記「已自動匯出名單」。"
        )
        area = st.selectbox("地區（決定寫入哪份名冊試算表）", areas, key="resume_latest_area")
        sheet_name = st.text_input("寫入的工作表名稱（例如：202608專員名冊）", key="resume_latest_sheet")

        if st.button("開始擷取最新雙北履歷", key="btn_extract_latest", type="primary"):
            if not sheet_name.strip():
                st.error("❗ 請輸入要寫入的工作表名稱")
            else:
                try:
                    with st.spinner("擷取中..."):
                        result = resume_system.extract_latest_resumes(area, sheet_name.strip())
                    st.success(f"✅ 成功擷取 {result['count']} 筆未讀履歷")
                    _show_mail_diagnostics(result)
                except Exception as exc:
                    st.error(f"❌ 擷取最新雙北履歷失敗：{exc}")
