from __future__ import annotations

import json
from typing import Any

import streamlit as st

from tools.local_agent_queue import create_task

from .invoice_payload_queue import enqueue_payload
from .pending_charge import DISPLAY_COLUMNS, get_pending_invoice_candidates


def _records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return [dict(row) for row in (value or [])]


def install(ui) -> None:
    """Use an ATM-refund-style picker before entering Invoice Center v2."""
    original_render = ui.render_invoice_create

    def _render_selected_top_query() -> tuple[str, str, str, str]:
        area = str(st.session_state.get("invoice_pending_area_key") or "taipei")
        area_label = str(st.session_state.get("invoice_pending_area_label") or "台北")
        order_no = str(st.session_state.get("invoice_center_order_no") or "")
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        invoice_type = st.session_state.get("invoice_center_invoice_type", "一般發票")
        with st.container(border=True):
            st.markdown('<div class="ic-section-title">查詢 Lemon 訂單</div>', unsafe_allow_html=True)
            cols = st.columns([1, 2.2, 1.2])
            cols[0].text_input("地區", value=area_label, disabled=True, key="invoice_selected_area_display")
            cols[1].text_input("Lemon 訂單號", value=order_no, disabled=True, key="invoice_selected_order_display")
            with cols[2]:
                invoice_type = st.selectbox("API 開立類型", list(ui.INVOICE_TYPE_OPTIONS.keys()), key="invoice_center_invoice_type")
            st.success("已自動查詢訂單並產生 Payload；鯨躍登入任務已送往 Local Agent。")
        return area, order_no, suffix, invoice_type

    def _start_invoice(area_label: str, area_key: str, queue: list[dict[str, Any]]) -> None:
        first = queue[0]
        order_no = str(first.get("_order_no") or first.get("G 訂單編號") or "").strip()
        if not order_no:
            raise ValueError("勾選資料缺少 G 訂單編號")
        ui._bootstrap_invoice_state()
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        ui._load_backend_order(area_key, order_no, suffix)
        rows = ui._normalize_line_items(st.session_state.get("invoice_center_line_items", []))
        totals = ui._calculate_totals(rows)
        payload = ui._build_payload(area_key, order_no, suffix, rows, totals)
        preview = ui.create_invoice_from_payload(payload, dry_run=True).payload
        preview_json = json.dumps(preview, ensure_ascii=False)
        st.session_state["invoice_pending_queue"] = queue
        st.session_state["invoice_pending_area_key"] = area_key
        st.session_state["invoice_pending_area_label"] = area_label
        st.session_state["invoice_center_order_no"] = order_no
        st.session_state["invoice_center_preview"] = preview
        st.session_state["invoice_create_started"] = True
        enqueue_payload(area_label, order_no, preview_json, created_by=st.session_state.get("username", "Tool System"))
        task = create_task("cetustek.login", {"area": area_label, "cdp_url": "http://127.0.0.1:9222"}, created_by=st.session_state.get("username", "Tool System"))
        st.session_state["invoice_cetustek_task_id"] = task.get("task_id", "")

    def render_invoice_create() -> None:
        if st.session_state.get("invoice_create_started"):
            ui._render_top_query = _render_selected_top_query
            original_render()
            if st.button("← 返回待開立清單", use_container_width=True):
                st.session_state.pop("invoice_create_started", None)
                st.session_state.pop("invoice_pending_queue", None)
                st.rerun()
            return
        options = ui.get_area_options()
        labels = [display for _key, display in options]
        key_by_label = {display: key for key, display in options}
        st.markdown("### 📍 執行區域")
        area_label = st.selectbox("執行區域", labels, label_visibility="collapsed", key="invoice_pending_picker_area")
        area_key = key_by_label[area_label]
        st.caption(f"只執行：{area_label}")
        try:
            candidates = get_pending_invoice_candidates(area_label)
            visible_keys = ["選取", *DISPLAY_COLUMNS]
            editor = st.data_editor(
                [{key: row.get(key, "") for key in visible_keys} for row in candidates],
                hide_index=True, use_container_width=True, disabled=list(DISPLAY_COLUMNS),
                column_config={"選取": st.column_config.CheckboxColumn("執行", default=False)},
                key=f"invoice_pending_picker_{area_label}",
            )
            selected = [row for row in _records(editor) if row.get("選取")]
            by_row = {row["列號"]: row for row in candidates}
            queue = [by_row[row["列號"]] for row in selected if row.get("列號") in by_row]
            st.caption(f"待開立發票：{len(candidates)} 筆；已勾選：{len(queue)} 筆")
        except Exception as exc:
            queue = []
            st.error(f"讀取清潔異動表失敗：{exc}")
        if st.button("▶ 執行", type="primary", use_container_width=True, disabled=not queue):
            try:
                _start_invoice(area_label, area_key, queue)
                st.rerun()
            except Exception as exc:
                st.error(f"發票流程啟動失敗：{exc}")

    ui.render_invoice_create = render_invoice_create
