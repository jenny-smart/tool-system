# -*- coding: utf-8 -*-
"""服務訂單系統內的檸檬保留單建單／取消 UI。"""

from datetime import date

import pandas as pd
import streamlit as st

from accounts import ACCOUNTS
from orders import get_region_by_address
from reserve_cancel import SYSTEM_RESERVE_MEMO, cancel_reserve_orders, find_reserve_orders
from reserve_optimizer import (
    PERIOD_HOURS,
    RESERVE_PHONE_DEFAULT,
    ReserveRule,
    build_period_plan,
    create_reserve_orders_for_plan,
    login_reserve_member,
    member_addresses,
)


def _require_supported_env(env: str) -> bool:
    if str(env or "").strip().lower() not in {"prod", "dev"}:
        st.error("檸檬保留單只支援 prod 正式機或 dev 測試機。")
        return False
    return True


def _env_label(env: str) -> str:
    return "正式機 prod" if str(env).lower() == "prod" else "測試機 dev"


def _lookup(env: str, backend_email: str, backend_password: str, phone: str):
    key = f"reserve_lookup::{env}::{phone}"
    cached = st.session_state.get(key)
    if cached:
        return cached
    result = login_reserve_member(env, backend_email.strip(), backend_password.strip(), phone.strip())
    st.session_state[key] = result
    return result


def _common_member_header(env: str, backend_email: str, backend_password: str):
    phone = st.text_input("保留單會員手機", value=RESERVE_PHONE_DEFAULT, key="reserve_shared_phone")
    st.info(f"目前執行環境：{_env_label(env)}")
    if not backend_email.strip() or not backend_password.strip():
        st.warning("請先在頁面上方輸入後台帳號與密碼。")
        return phone, None
    try:
        lookup = _lookup(env, backend_email, backend_password, phone)
    except Exception as exc:
        st.error(f"讀取保留會員失敗：{exc}")
        return phone, None
    return phone, lookup


def _parse_plan_row_spec(value, max_row):
    text = str(value or "").strip().replace("，", ",")
    if not text:
        raise ValueError("請輸入分析列號。")
    selected = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            bounds = [x.strip() for x in part.split("-", 1)]
            if len(bounds) != 2 or not all(x.isdigit() for x in bounds):
                raise ValueError(f"列號格式錯誤：{part}")
            start, end = map(int, bounds)
            if start < 1 or end < start:
                raise ValueError(f"列號範圍錯誤：{part}")
            selected.update(range(start, end + 1))
        elif part.isdigit() and int(part) > 0:
            selected.add(int(part))
        else:
            raise ValueError(f"列號格式錯誤：{part}")
    invalid = sorted(x for x in selected if x > max_row)
    if invalid:
        raise ValueError("超出分析結果列號：" + "、".join(map(str, invalid)))
    return selected


def render_reserve_create(backend_email: str, backend_password: str, env: str) -> None:
    st.markdown("### 🍋 檸檬保留單建單")
    st.caption("依期間統計未配班人力、設定 AM/PM 保留率，再選擇真正要成立訂單的日期範圍。")
    if not _require_supported_env(env):
        return
    phone, lookup = _common_member_header(env, backend_email, backend_password)
    if not lookup:
        return
    addresses = member_addresses(lookup)
    if not addresses:
        st.error(f"保留會員 {phone} 在{_env_label(env)}沒有可用地址。")
        return
    c1, c2 = st.columns(2)
    with c1:
        address = st.selectbox("保留單服務地址", addresses, key="reserve_create_address")
    with c2:
        payway = st.selectbox("保留單付款類型", ["儲值金", "信用卡", "ATM"], index=0, key="reserve_create_payway")
    region = get_region_by_address(address, ACCOUNTS) or "台北"
    st.caption(f"地址判斷區域：{region}｜客人備註會自動寫入「{SYSTEM_RESERVE_MEMO}」｜客服備註固定「大掃除檸檬保留單」")
    st.markdown("#### 1. 分析期間與保留率")
    d1, d2 = st.columns(2)
    with d1:
        start = st.date_input("分析開始日期", value=date(2026, 9, 21), key="reserve_plan_start")
    with d2:
        end = st.date_input("分析結束日期", value=date(2026, 9, 30), key="reserve_plan_end")
    periods = st.multiselect("要分析的時段", list(PERIOD_HOURS.keys()), default=["09:00-12:00", "14:00-17:00"], key="reserve_plan_periods")
    r1, r2 = st.columns(2)
    with r1:
        am_rate = st.slider("AM 保留率", 0, 100, 70, 5, key="reserve_am_rate") / 100.0
    with r2:
        pm_rate = st.slider("PM 保留率", 0, 100, 70, 5, key="reserve_pm_rate") / 100.0
    if st.button("統計班表並產生保留計畫", width="stretch", key="reserve_build_plan"):
        analysis_status = st.status("已收到分析指令，正在讀取班表…", expanded=True)
        try:
            analysis_status.write(f"分析期間：{start}～{end}；時段：{'、'.join(periods) or '未選擇'}")
            rules = [ReserveRule(start=start, end=end, am_rate=am_rate, pm_rate=pm_rate, label="保留區間")]
            plan = build_period_plan(lookup, start, end, rules, periods)
            st.session_state.reserve_menu_plan = [p.__dict__ for p in plan]
            st.session_state.pop("reserve_create_editor_rows", None)
            st.session_state.reserve_create_editor_revision = st.session_state.get("reserve_create_editor_revision", 0) + 1
            analysis_status.update(label=f"分析完成：共 {len(plan)} 個日期／時段", state="complete", expanded=False)
        except Exception as exc:
            message = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            analysis_status.update(label="分析失敗", state="error", expanded=True)
            analysis_status.write(message)
            st.error(f"分析失敗：{message}")
    plan_rows = st.session_state.get("reserve_menu_plan") or []
    if not plan_rows:
        return
    editor_rows = st.session_state.get("reserve_create_editor_rows")
    if not editor_rows or len(editor_rows) != len(plan_rows):
        editor_rows = [{"執行": False, "列號": i, "日期": r["service_date"], "時段": r["period"], "未配班人數": r["unassigned_people"], "保留率": f"{int(r['reserve_rate'] * 100)}%", "建議保留張數": r["reserve_order_target"], "建立張數": r["reserve_order_target"], "預計留給市場": r["market_people_target"]} for i, r in enumerate(plan_rows, 1)]
        st.session_state.reserve_create_editor_rows = editor_rows
    st.markdown("#### 2. 選擇日期 × 時段與建立張數")
    st.caption("可逐列勾選、輸入分析列號，或使用全選／全不選；『建立張數』可低於建議值。")
    row_input = st.text_input("執行分析列號（選填）", placeholder="例如：1,2 或 1-5", key="reserve_create_row_spec")
    b1, b2, b3 = st.columns(3)
    action = None
    with b1:
        if st.button("套用列號", width="stretch", key="reserve_create_apply_rows"): action = "rows"
    with b2:
        if st.button("全選", width="stretch", key="reserve_create_select_all"): action = "all"
    with b3:
        if st.button("全不選", width="stretch", key="reserve_create_clear_all"): action = "none"
    if action:
        try:
            picked = _parse_plan_row_spec(row_input, len(editor_rows)) if action == "rows" else (set(range(1, len(editor_rows) + 1)) if action == "all" else set())
            for item in editor_rows:
                item["執行"] = item["列號"] in picked
            st.session_state.reserve_create_editor_rows = editor_rows
            st.session_state.reserve_create_editor_revision = st.session_state.get("reserve_create_editor_revision", 0) + 1
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    revision = st.session_state.get("reserve_create_editor_revision", 0)
    edited = st.data_editor(pd.DataFrame(editor_rows), width="stretch", hide_index=True, disabled=["列號", "日期", "時段", "未配班人數", "保留率", "建議保留張數", "預計留給市場"], column_config={"執行": st.column_config.CheckboxColumn("執行"), "建立張數": st.column_config.NumberColumn("建立張數", min_value=0, step=1)}, key=f"reserve_create_editor_{revision}")
    st.session_state.reserve_create_editor_rows = edited.to_dict("records")
    selected_rows = [{"service_date": x["日期"], "period": x["時段"], "reserve_order_target": int(x["建立張數"])} for x in edited.to_dict("records") if x.get("執行") and int(x.get("建立張數") or 0) > 0]
    total_orders = sum(x["reserve_order_target"] for x in selected_rows)
    st.info(f"目前選擇 {len(selected_rows)} 個日期／時段，預計建立 {total_orders} 張保留單。")
    machine = _env_label(env)
    confirm = st.checkbox(f"我確認要在{machine}建立以上 {total_orders} 張檸檬保留單", key="reserve_create_confirm")
    if st.button("確認建立檸檬保留單", type="primary", width="stretch", disabled=not confirm or total_orders <= 0, key="reserve_create_execute"):
        execution_status = st.status(f"已收到建立指令，準備建立 {total_orders} 張保留單…", expanded=True)
        try:
            execution_status.write("正在逐日期／時段重新確認即時班表並建立訂單…")
            with st.spinner(f"正在{machine}逐張重新確認班表並建立保留單..."):
                result = create_reserve_orders_for_plan(env_name=env, lookup_result=lookup, region=region, address=address, plan_rows=selected_rows, payway=payway, continue_after_slot_error=True)
            st.session_state.reserve_menu_last_create = result
            execution_status.update(label=f"建立完成：成功 {result.get('success_count', 0)} / {result.get('target_orders', 0)} 張", state="complete", expanded=False)
            st.success(f"建立完成：成功 {result.get('success_count', 0)} / {result.get('target_orders', 0)} 張")
        except Exception as exc:
            message = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            execution_status.update(label="建立失敗", state="error", expanded=True)
            execution_status.write(message)
            st.error(f"建立失敗：{message}")
    result = st.session_state.get("reserve_menu_last_create")
    if result and result.get("results"):
        st.dataframe(pd.DataFrame(result["results"]), width="stretch", hide_index=True)


def render_reserve_cancel(backend_email: str, backend_password: str, env: str) -> None:
    st.markdown("### 🗑️ 檸檬保留單取消")
    st.caption("依期間、複選時段與客人備註篩選保留單，再勾選實際要取消的訂單。取消前會重新檢查最新客人備註。")
    if not _require_supported_env(env):
        return
    phone, lookup = _common_member_header(env, backend_email, backend_password)
    if not lookup:
        return
    c1, c2 = st.columns(2)
    with c1:
        cancel_start = st.date_input("取消查詢開始日期", value=date(2026, 9, 21), key="reserve_cancel_start")
    with c2:
        cancel_end = st.date_input("取消查詢結束日期", value=date(2026, 9, 30), key="reserve_cancel_end")
    cancel_periods = st.multiselect("取消查詢時段（可複選；不選代表全部時段）", list(PERIOD_HOURS.keys()), default=[], key="reserve_cancel_periods")
    memo_filter = st.selectbox("客人備註篩選", ["僅系統保留單", "系統保留單或空白", "僅空白", "全部（僅供查看）"], index=0, key="reserve_cancel_memo_filter")

    if st.button("查詢可取消保留單", width="stretch", key="reserve_cancel_search"):
        search_status = st.status("已收到查詢指令，正在搜尋保留單…", expanded=True)
        st.session_state.reserve_menu_cancel_rows = []
        st.session_state.reserve_menu_cancel_debug = {}
        st.session_state.reserve_cancel_selected_order_nos = []
        try:
            search_status.write("正在讀取訂單並逐筆確認最新客人備註…")
            with st.spinner("正在查詢保留單：先抓近期訂單，再逐筆確認客人備註..."):
                rows, debug = find_reserve_orders(
                    env, backend_email.strip(), backend_password.strip(), phone.strip(),
                    cancel_start.isoformat(), cancel_end.isoformat(),
                    memo_filter=memo_filter, periods=cancel_periods, return_debug=True,
                )
            st.session_state.reserve_menu_cancel_rows = rows
            st.session_state.reserve_menu_cancel_filter = memo_filter
            st.session_state.reserve_menu_cancel_debug = debug
            if rows:
                search_status.update(label=f"查詢完成：找到 {len(rows)} 張", state="complete", expanded=False)
                st.success(f"查詢完成，共找到 {len(rows)} 張符合條件的保留單。")
            else:
                search_status.update(label="查詢完成：沒有符合條件的保留單", state="complete", expanded=False)
                st.warning("查詢完成，但此期間與篩選條件沒有符合的保留單。請查看下方查詢診斷。")
        except Exception as exc:
            message = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            st.session_state.reserve_menu_cancel_rows = []
            st.session_state.reserve_menu_cancel_debug = {"error": message}
            search_status.update(label="查詢失敗", state="error", expanded=True)
            search_status.write(message)
            st.error(f"查詢失敗：{message}")

    debug = st.session_state.get("reserve_menu_cancel_debug") or {}
    if debug:
        with st.expander("查詢診斷", expanded=not bool(st.session_state.get("reserve_menu_cancel_rows"))):
            if debug.get("error"):
                st.error(debug["error"])
            else:
                st.write(
                    f"已掃描 {debug.get('pages_scanned', 0)} 頁 / {debug.get('cards_scanned', 0)} 張卡片；"
                    f"解析 {debug.get('orders_parsed', 0)} 張訂單；"
                    f"日期＋時段符合 {debug.get('date_period_candidates', 0)} 張；"
                    f"客人備註符合 {debug.get('memo_matches', 0)} 張；"
                    f"明細讀取失敗 {debug.get('detail_errors', 0)} 張。"
                )

    rows = st.session_state.get("reserve_menu_cancel_rows") or []
    if not rows:
        return
    df = pd.DataFrame(rows)
    cols = [c for c in ["service_date", "period", "order_no", "customer_memo", "cancel_eligible"] if c in df.columns]
    preview = df[cols].copy()
    preview.rename(columns={"service_date": "日期", "period": "時段", "order_no": "訂單編號", "customer_memo": "客人備註", "cancel_eligible": "安全可取消"}, inplace=True)
    st.dataframe(preview, width="stretch", hide_index=True)

    safe_rows = [r for r in rows if r.get("cancel_eligible")]
    st.write(f"查到 **{len(rows)} 張**；目前符合安全取消條件 **{len(safe_rows)} 張**。")
    if st.session_state.get("reserve_menu_cancel_filter") == "全部（僅供查看）":
        st.warning("『全部（僅供查看）』不開放取消，請改用其他客人備註篩選後重新查詢。")
        return
    if not safe_rows:
        return

    st.markdown("#### 選擇要取消的保留單")
    order_map = {str(r.get("order_no") or ""): r for r in safe_rows if str(r.get("order_no") or "")}
    order_nos = list(order_map.keys())

    # 清理舊查詢留下、已不存在於目前結果的選取值。
    current_selected = [
        x for x in st.session_state.get("reserve_cancel_selected_order_nos", [])
        if x in order_map
    ]
    st.session_state.reserve_cancel_selected_order_nos = current_selected

    b1, b2, _ = st.columns([1, 1, 3])
    with b1:
        if st.button("✅ 全選可取消單", width="stretch", key="reserve_cancel_select_all"):
            st.session_state.reserve_cancel_selected_order_nos = order_nos[:]
    with b2:
        if st.button("⬜ 全部取消選取", width="stretch", key="reserve_cancel_clear_all"):
            st.session_state.reserve_cancel_selected_order_nos = []

    def _format_cancel_option(order_no: str) -> str:
        row = order_map.get(order_no, {})
        memo = str(row.get("customer_memo") or "").strip() or "空白"
        return f"{row.get('service_date', '')}｜{row.get('period', '')}｜{order_no}｜客人備註：{memo}"

    selected_order_nos = st.multiselect(
        "勾選實際要取消的訂單（可複選）",
        options=order_nos,
        format_func=_format_cancel_option,
        key="reserve_cancel_selected_order_nos",
        placeholder="請選擇要取消的保留單",
    )
    selected_rows = [order_map[order_no] for order_no in selected_order_nos if order_no in order_map]
    st.info(f"目前已選 **{len(selected_rows)} 張**；可取消總數 {len(safe_rows)} 張。")

    if selected_rows:
        selected_df = pd.DataFrame(selected_rows)
        cols2 = [c for c in ["service_date", "period", "order_no", "customer_memo"] if c in selected_df.columns]
        selected_preview = selected_df[cols2].copy()
        selected_preview.rename(columns={"service_date": "日期", "period": "時段", "order_no": "訂單編號", "customer_memo": "客人備註"}, inplace=True)
        st.caption("本次將取消以下訂單：")
        st.dataframe(selected_preview, width="stretch", hide_index=True)

    machine = _env_label(env)
    selected_count = len(selected_rows)
    confirm = st.checkbox(
        f"我確認要在{machine}取消以上 {selected_count} 張檸檬保留單",
        key="reserve_cancel_confirm",
        disabled=selected_count <= 0,
    )
    if st.button(
        "確認取消檸檬保留單",
        type="primary",
        width="stretch",
        disabled=not confirm or selected_count <= 0,
        key="reserve_cancel_execute",
    ):
        cancel_status = st.status(f"已收到取消指令，準備處理 {selected_count} 張保留單…", expanded=True)
        try:
            cancel_status.write("正在逐張重新確認最新客人備註並取消…")
            with st.spinner(f"正在{machine}逐張重新檢查客人備註並取消..."):
                # 只把使用者實際勾選的 rows 傳入；count 等於所選張數，因此
                # cancel_reserve_orders 不會碰到未選取的安全可取消單。
                results = cancel_reserve_orders(
                    env,
                    backend_email.strip(),
                    backend_password.strip(),
                    selected_rows,
                    selected_count,
                )
            st.session_state.reserve_menu_cancel_result = results
            st.session_state.reserve_cancel_selected_order_nos = []
            cancel_status.update(label=f"取消流程完成：處理 {len(results)} 張", state="complete", expanded=False)
            st.success("取消流程執行完成；若備註已被改成人工客人保留內容，該筆會自動跳過。")
        except Exception as exc:
            message = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            cancel_status.update(label="取消失敗", state="error", expanded=True)
            cancel_status.write(message)
            st.error(f"取消失敗：{message}")

    results = st.session_state.get("reserve_menu_cancel_result") or []
    if results:
        st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)
