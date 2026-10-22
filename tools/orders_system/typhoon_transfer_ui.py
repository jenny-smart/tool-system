# -*- coding: utf-8 -*-
"""服務訂單系統內的「颱風假訂單移轉」UI。"""

import pandas as pd
import streamlit as st

import typhoon_transfer as tt


def _rows_to_frame(rows):
    return pd.DataFrame([
        {
            "移轉": True,
            "順序": idx,
            "時段": row["period"],
            "訂單編號": row["order_no"],
            "姓名": row["name"],
            "電話": row["phone"],
            "人數": row["person"],
            "專員": row["staff"],
            "付款狀態": row["payment_status"],
        }
        for idx, row in enumerate(rows, 1)
    ])


def render(backend_email, backend_password, env, copy_button=None):
    st.caption(
        "依颱風假當天的時段，逐筆用後台「修改日期」移到移轉區間：先選日期、時段維持原時段。"
        "名單為後台「服務日期＋已付款」搜尋結果由上而下的順序，依序排入，第一天排不下才換下一天。"
    )
    c1, c2 = st.columns(2)
    with c1:
        typhoon_text = st.text_input("颱風假日期", value="", placeholder="2026/10/23", key="tt_typhoon_date")
    with c2:
        range_text = st.text_input("移轉日期區間", value="", placeholder="2027/03/01-2027/03/05", key="tt_range")
    day_type = st.selectbox("移轉日別", tt.DAY_TYPES, key="tt_day_type",
                            help="平日（週一～五，國定假日也算平日）與週末（週六、日）價格不同，預設只移到與颱風假相同日別的日期。")

    if st.button("🔍 查詢颱風假當天訂單", type="primary", use_container_width=True, key="tt_search"):
        if not backend_email.strip() or not backend_password.strip():
            st.error("請先在上方輸入後台帳號密碼")
            return
        try:
            typhoon_date = tt._parse_date(typhoon_text).strftime("%Y-%m-%d")
        except Exception:
            st.error("颱風假日期格式請輸入：2026/10/23")
            return
        try:
            with st.spinner("登入後台並查詢訂單…"):
                rows, debug = tt.find_typhoon_orders(env, backend_email.strip(), backend_password.strip(), typhoon_date)
        except Exception as exc:
            st.error(f"查詢失敗：{exc}")
            return
        st.session_state.tt_rows = rows
        st.session_state.tt_debug = debug
        st.session_state.tt_loaded_date = typhoon_date
        st.session_state.pop("tt_results", None)
        st.session_state.pop("tt_editor", None)

    rows = st.session_state.get("tt_rows")
    if rows is None:
        return
    debug = st.session_state.get("tt_debug", {})
    typhoon_date = st.session_state.get("tt_loaded_date", "")
    st.caption(f"後台：{debug.get('base_url', '')}｜{typhoon_date} 共 {len(rows)} 筆已付款服務訂單")
    if debug.get("hit_page_limit"):
        st.warning("查詢已達頁數上限，名單可能不完整，請到後台確認。")
    if not rows:
        st.success("颱風假當天沒有已付款的服務訂單。")
        return

    st.markdown("**移轉名單**（依後台列表由上而下；可取消勾選，或改「順序」調整先後）")
    edited = st.data_editor(
        _rows_to_frame(rows), hide_index=True, use_container_width=True, key="tt_editor",
        disabled=["時段", "訂單編號", "姓名", "電話", "人數", "專員", "付款狀態"],
        column_config={
            "移轉": st.column_config.CheckboxColumn("移轉"),
            "順序": st.column_config.NumberColumn("順序", min_value=1, step=1),
        },
    )

    try:
        start, end = tt.parse_date_range(range_text)
        dates = tt.target_dates(start, end, day_type, typhoon_date)
    except Exception as exc:
        st.info(f"請輸入移轉日期區間：{exc}" if range_text.strip() else "請輸入移轉日期區間，例如 2027/03/01-2027/03/05")
        return
    if not dates:
        st.error("移轉區間內沒有符合日別的日期")
        return
    if typhoon_date in dates:
        st.error("移轉區間不可包含颱風假當天")
        return
    st.caption("候選日期（依序嘗試）：" + "、".join(dates))

    by_order = {row["order_no"]: row for row in rows}
    selected = edited[edited["移轉"]].copy()
    selected = selected.sort_values("順序", kind="stable")
    plan = [by_order[no] for no in selected["訂單編號"]]

    confirm = st.checkbox(f"確認要把 {len(plan)} 筆訂單的服務日期改到 {dates[0]}～{dates[-1]}（時段不變）", key="tt_confirm")
    if st.button("🌀 開始移轉", type="primary", use_container_width=True, disabled=not (confirm and plan), key="tt_run"):
        bar = st.progress(0.0)
        status = st.empty()

        def _progress(index, total, row):
            bar.progress((index - 1) / total)
            status.write(f"處理第 {index}/{total} 筆：{row['period']} {row['name']}（{row['order_no']}）")

        try:
            results = tt.transfer_typhoon_orders(
                env, backend_email.strip(), backend_password.strip(), typhoon_date, plan, dates, progress=_progress,
            )
        except Exception as exc:
            st.error(f"移轉失敗：{exc}")
            return
        bar.progress(1.0)
        status.empty()
        st.session_state.tt_results = results

    results = st.session_state.get("tt_results")
    if results:
        ok = sum(1 for r in results if r.get("success"))
        (st.success if ok == len(results) else st.warning)(f"完成：成功 {ok} 筆，失敗 {len(results) - ok} 筆")
        copy_rows = tt.result_copy_rows(results)
        st.dataframe(pd.DataFrame([
            {
                "結果": "✅" if r.get("success") else "❌",
                **{k: v for k, v in c.items() if k not in ("結果", "說明")},
                "新專員": r.get("new_staff", ""),
                "說明": r.get("message", ""),
                "略過日期": "；".join(r.get("attempts") or []),
            }
            for r, c in zip(results, copy_rows)
        ]), hide_index=True, use_container_width=True)
        copy_label = "複製原日期時段／移轉後日期時段（貼到 Google Sheets 會自動分欄）"
        if copy_button:
            copy_button(copy_label, tt.results_tsv(results), "tt_copy_results")
        else:
            st.caption(copy_label)
            st.code(tt.results_tsv(results), language=None)
