from __future__ import annotations

from typing import Any

import streamlit as st

from .pending_charge import DISPLAY_COLUMNS, get_pending_invoice_candidates


def _records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return [dict(row) for row in (value or [])]


def install(ui) -> None:
    """Patch invoice create query so area/pending rows are selected before Lemon lookup."""

    def _render_top_query() -> tuple[str, str, str, str]:
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        options = ui.get_area_options()
        labels = [display for _key, display in options]
        key_by_label = {display: key for key, display in options}

        with st.container(border=True):
            st.markdown('<div class="ic-section-title">待開立異動資料</div>', unsafe_allow_html=True)
            area_label = st.selectbox("地區", labels, key="invoice_pending_area_label", help="先選地區，再從該地區清潔異動表篩選待開立資料。")
            area = key_by_label[area_label]

            try:
                candidates = get_pending_invoice_candidates(area_label)
            except Exception as exc:
                candidates = []
                st.error(f"讀取{area_label}異動資料失敗：{exc}")

            visible_keys = ["選取", *DISPLAY_COLUMNS]
            visible_rows = [{key: row.get(key, "") for key in visible_keys} for row in candidates]
            if visible_rows:
                edited = st.data_editor(
                    visible_rows,
                    use_container_width=True,
                    hide_index=True,
                    key=f"invoice_pending_editor_{area_label}",
                    column_config={
                        "選取": st.column_config.CheckboxColumn("開立", default=False),
                        "列號": st.column_config.NumberColumn("列號", disabled=True),
                        "B 狀態": st.column_config.TextColumn("B 狀態", disabled=True),
                        "G 訂單編號": st.column_config.TextColumn("G 訂單編號", disabled=True),
                        "H 客戶": st.column_config.TextColumn("H 客戶", disabled=True),
                        "K 後台備註": st.column_config.TextColumn("K 後台備註", disabled=True),
                        "M 收款時間": st.column_config.TextColumn("M 收款時間", disabled=True),
                        "N 收款金額": st.column_config.TextColumn("N 收款金額", disabled=True),
                    },
                    disabled=list(DISPLAY_COLUMNS),
                )
                selected_rows = [row for row in _records(edited) if row.get("選取")]
                by_row_no = {row["列號"]: row for row in candidates}
                queue = [by_row_no[row["列號"]] for row in selected_rows if row.get("列號") in by_row_no]
                if queue:
                    st.session_state["invoice_pending_queue"] = queue
                    st.session_state["invoice_center_order_no"] = queue[0]["_order_no"]
                    st.caption(f"已選 {len(queue)} 筆；目前先帶入第 1 筆訂單 {queue[0]['_order_no']}。")
            else:
                st.info("目前沒有符合條件的資料（B=待收款、G有訂單編號、O發票號碼空白）。")

        with st.container(border=True):
            st.markdown('<div class="ic-section-title">查詢 Lemon 訂單</div>', unsafe_allow_html=True)
            cols = st.columns([1, 2.2, 1.2, 1])
            with cols[0]:
                st.text_input("地區", value=area_label, disabled=True, key="invoice_center_area_display")
            with cols[1]:
                order_no = st.text_input("Lemon 訂單號", key="invoice_center_order_no", placeholder="勾選上方資料後會自動帶入")
            with cols[2]:
                invoice_type = st.selectbox("API 開立類型", list(ui.INVOICE_TYPE_OPTIONS.keys()), key="invoice_center_invoice_type")
            with cols[3]:
                st.write("")
                st.write("")
                if st.button("🔍 查詢", type="primary", use_container_width=True):
                    try:
                        ui._load_backend_order(area, order_no, suffix)
                        st.success("已查詢並帶入訂單資料")
                    except Exception as exc:
                        st.session_state.pop("invoice_center_backend_order", None)
                        st.error(f"查詢失敗：{exc}")
            with st.expander("進階設定", expanded=False):
                suffix = st.text_input("EI orderid suffix", value=suffix, key="invoice_center_order_suffix", help="一般使用不需調整")
        return area, order_no, suffix, invoice_type

    ui._render_top_query = _render_top_query
