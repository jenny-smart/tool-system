from __future__ import annotations

import json
from typing import Any

import streamlit as st

from tools.local_agent_queue import create_task

from .invoice_payload_queue import enqueue_payload
from .pending_charge import DISPLAY_COLUMNS, get_pending_invoice_candidates


DELIVERY_OPTIONS = ["會員載具", "手機載具", "自然人憑證", "紙本", "捐贈"]
BUYER_OPTIONS = ["自然人", "公司"]


def _records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return [dict(row) for row in (value or [])]


def install(ui) -> None:
    """Pending invoice picker with optional per-row overrides, then one-click batch dispatch."""

    def _carrier_value() -> str:
        method = st.session_state.get("invoice_center_delivery_method", "會員載具")
        if method == "手機載具":
            return str(st.session_state.get("invoice_center_mobile_barcode") or "")
        if method == "自然人憑證":
            return str(st.session_state.get("invoice_center_citizen_cert") or "")
        if method == "捐贈":
            return str(st.session_state.get("invoice_center_donate_code") or "")
        if method == "會員載具":
            return str(st.session_state.get("invoice_center_member_carrier") or "")
        return ""

    def _current_invoice_settings(area_key: str, order_no: str) -> dict[str, str]:
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        _order, payload = ui.fetch_backend_order_invoice_payload(area_key, order_no, suffix=suffix)
        buyer_identifier = str(getattr(payload, "buyer_identifier", "") or "").strip()
        buyer_name = str(getattr(payload, "buyer_name", "") or "").strip()
        carrier_type = str(getattr(payload, "carriertype", "") or "").strip()
        carrier_no = str(getattr(payload, "carrierid1", "") or "").strip()
        donate = str(getattr(payload, "donate", "") or "").strip()
        donate_code = str(getattr(payload, "donatevat", "") or "").strip()

        if buyer_identifier:
            delivery = "紙本"
            carrier_value = ""
            buyer_type = "公司"
        elif donate == "1" or donate_code:
            delivery = "捐贈"
            carrier_value = donate_code
            buyer_type = "自然人"
        elif carrier_type == "3J0002":
            delivery = "手機載具"
            carrier_value = carrier_no
            buyer_type = "自然人"
        elif carrier_type == "CQ0001":
            delivery = "自然人憑證"
            carrier_value = carrier_no
            buyer_type = "自然人"
        elif carrier_type:
            delivery = "會員載具"
            carrier_value = carrier_no
            buyer_type = "自然人"
        else:
            delivery = "紙本"
            carrier_value = ""
            buyer_type = "自然人"

        return {
            "發票對象": buyer_type,
            "發票方式": delivery,
            "公司抬頭": buyer_name if buyer_type == "公司" else "",
            "統編": buyer_identifier,
            "載具/捐贈碼": carrier_value,
            "API開立類型": "一般發票",
        }

    def _apply_override(row: dict[str, Any]) -> None:
        buyer_type = str(row.get("發票對象") or "自然人")
        delivery = str(row.get("發票方式") or "會員載具")
        st.session_state["invoice_center_buyer_type"] = buyer_type
        st.session_state["invoice_center_delivery_method"] = delivery
        st.session_state["invoice_center_invoice_type"] = str(row.get("API開立類型") or "一般發票")

        if buyer_type == "公司":
            company_title = str(row.get("公司抬頭") or "").strip()
            st.session_state["invoice_center_company_title"] = company_title
            st.session_state["invoice_center_buyer_name"] = company_title
            st.session_state["invoice_center_buyer_identifier"] = str(row.get("統編") or "").strip()
        else:
            st.session_state["invoice_center_buyer_identifier"] = ""
            st.session_state["invoice_center_company_title"] = ""

        carrier = str(row.get("載具/捐贈碼") or "").strip()
        st.session_state["invoice_center_member_carrier"] = ""
        st.session_state["invoice_center_mobile_barcode"] = ""
        st.session_state["invoice_center_citizen_cert"] = ""
        st.session_state["invoice_center_donate_code"] = ""
        if delivery == "會員載具":
            st.session_state["invoice_center_member_carrier"] = carrier
        elif delivery == "手機載具":
            st.session_state["invoice_center_mobile_barcode"] = carrier
        elif delivery == "自然人憑證":
            st.session_state["invoice_center_citizen_cert"] = carrier
        elif delivery == "捐贈":
            st.session_state["invoice_center_donate_code"] = carrier

    def _build_payload(area_key: str, order_no: str, override: dict[str, Any] | None) -> dict[str, Any]:
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        ui._load_backend_order(area_key, order_no, suffix)
        if override is not None:
            _apply_override(override)
        rows = ui._normalize_line_items(st.session_state.get("invoice_center_line_items", []))
        totals = ui._calculate_totals(rows)
        payload = ui._build_payload(area_key, order_no, suffix, rows, totals)
        return ui.create_invoice_from_payload(payload, dry_run=True).payload

    def _dispatch(rows: list[dict[str, Any]], area_label: str, area_key: str) -> None:
        created_by = st.session_state.get("username", "Tool System")
        sent = 0
        for row in rows:
            order_no = str(row.get("G 訂單編號") or row.get("_order_no") or "").strip()
            if not order_no:
                continue
            override = row if bool(row.get("變更發票")) else None
            preview = _build_payload(area_key, order_no, override)
            enqueue_payload(area_label, order_no, json.dumps(preview, ensure_ascii=False), created_by=created_by)
            sent += 1
        if not sent:
            raise ValueError("沒有可送出的 Payload")

        task = create_task(
            "cetustek.login",
            {"area": area_label, "cdp_url": "http://127.0.0.1:9222"},
            created_by=created_by,
        )
        st.session_state["invoice_cetustek_task_id"] = task.get("task_id", "")
        st.session_state["invoice_agent_dispatched"] = True

    def render_invoice_create() -> None:
        options = ui.get_area_options()
        labels = [display for _key, display in options]
        key_by_label = {display: key for key, display in options}

        st.markdown("### 📍 執行區域")
        area_label = st.selectbox("執行區域", labels, label_visibility="collapsed", key="invoice_pending_picker_area")
        area_key = key_by_label[area_label]
        st.caption(f"只執行：{area_label}")

        try:
            candidates = get_pending_invoice_candidates(area_label)
            select_all = st.checkbox("全選", key=f"invoice_pending_select_all_{area_label}")
            visible_rows = []
            for source in candidates:
                item = {key: source.get(key, "") for key in DISPLAY_COLUMNS}
                order_no = str(source.get("G 訂單編號") or source.get("_order_no") or "").strip()
                current = _current_invoice_settings(area_key, order_no) if order_no else {
                    "發票對象": "自然人",
                    "發票方式": "紙本",
                    "公司抬頭": "",
                    "統編": "",
                    "載具/捐贈碼": "",
                    "API開立類型": "一般發票",
                }
                item.update({
                    "選取": bool(select_all),
                    "變更發票": False,
                    **current,
                })
                visible_rows.append(item)

            editor = st.data_editor(
                visible_rows,
                hide_index=True,
                use_container_width=True,
                disabled=list(DISPLAY_COLUMNS),
                column_order=["選取", *DISPLAY_COLUMNS, "變更發票", "發票對象", "發票方式", "公司抬頭", "統編", "載具/捐贈碼", "API開立類型"],
                column_config={
                    "選取": st.column_config.CheckboxColumn("執行", default=False),
                    "變更發票": st.column_config.CheckboxColumn("變更發票", help="不勾選＝完全沿用訂單原發票設定"),
                    "發票對象": st.column_config.SelectboxColumn("發票對象", options=BUYER_OPTIONS),
                    "發票方式": st.column_config.SelectboxColumn("發票方式", options=DELIVERY_OPTIONS),
                    "公司抬頭": st.column_config.TextColumn("公司抬頭"),
                    "統編": st.column_config.TextColumn("統編"),
                    "載具/捐贈碼": st.column_config.TextColumn("載具/捐贈碼"),
                    "API開立類型": st.column_config.SelectboxColumn("API開立類型", options=list(ui.INVOICE_TYPE_OPTIONS.keys())),
                },
                key=f"invoice_pending_picker_{area_label}_{int(select_all)}",
            )
            edited_rows = _records(editor)
            selected = [row for row in edited_rows if row.get("選取")]
            by_row = {row["列號"]: row for row in candidates}
            queue: list[dict[str, Any]] = []
            for row in selected:
                source = by_row.get(row.get("列號"))
                if not source:
                    continue
                merged = dict(source)
                merged.update(row)
                queue.append(merged)
            st.caption(f"待開立發票：{len(candidates)} 筆；已勾選：{len(queue)} 筆")
            st.caption("發票欄位顯示訂單目前設定；需要改紙本／載具／統編等才勾「變更發票」。")
        except Exception as exc:
            queue = []
            st.error(f"讀取清潔異動表失敗：{exc}")

        invalid = []
        for row in queue:
            if not row.get("變更發票"):
                continue
            if row.get("發票對象") == "公司" and not str(row.get("統編") or "").strip():
                invalid.append(str(row.get("G 訂單編號") or ""))
        if invalid:
            st.warning("公司發票尚缺統編：" + "、".join(invalid))

        if st.button("▶ 執行", type="primary", use_container_width=True, disabled=not queue or bool(invalid)):
            try:
                with st.status("正在建立發票資料並送往本機 Agent…", expanded=True) as status:
                    st.write(f"地區：{area_label}；勾選：{len(queue)} 筆")
                    _dispatch(queue, area_label, area_key)
                    status.update(label="已送出，等待本機 Agent 連續處理鯨躍", state="complete")
                st.success(f"已送往本機 Agent：{len(queue)} 筆；後續不需逐筆確認")
            except Exception as exc:
                st.error(f"送出失敗：{exc}")

    ui.render_invoice_create = render_invoice_create
