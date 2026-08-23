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
    """Pending-change picker -> execute -> invoice review/preview -> Local Agent."""

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

    def _apply_review_settings(row: dict[str, Any]) -> None:
        buyer_type = str(row.get("發票對象") or "自然人")
        delivery = str(row.get("發票方式") or "會員載具")
        st.session_state["invoice_center_buyer_type"] = buyer_type
        st.session_state["invoice_center_delivery_method"] = delivery
        st.session_state["invoice_center_invoice_type"] = str(row.get("API開立類型") or "一般發票")

        if buyer_type == "公司":
            company_title = str(row.get("公司抬頭") or "").strip()
            identifier = str(row.get("統編") or "").strip()
            st.session_state["invoice_center_company_title"] = company_title
            st.session_state["invoice_center_buyer_name"] = company_title
            st.session_state["invoice_center_buyer_identifier"] = identifier
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

    def _build_preview(area_key: str, order_no: str, row: dict[str, Any]) -> dict[str, Any]:
        suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
        ui._load_backend_order(area_key, order_no, suffix)
        _apply_review_settings(row)
        rows = ui._normalize_line_items(st.session_state.get("invoice_center_line_items", []))
        totals = ui._calculate_totals(rows)
        payload = ui._build_payload(area_key, order_no, suffix, rows, totals)
        return ui.create_invoice_from_payload(payload, dry_run=True).payload

    def _prepare_invoice(area_label: str, area_key: str, queue: list[dict[str, Any]]) -> None:
        prepared: list[dict[str, Any]] = []
        previews: dict[str, dict[str, Any]] = {}
        for item in queue:
            order_no = str(item.get("_order_no") or item.get("G 訂單編號") or "").strip()
            if not order_no:
                continue
            suffix = str(st.session_state.get("invoice_center_order_suffix", "-1") or "-1")
            order = ui._load_backend_order(area_key, order_no, suffix)
            review = {
                "訂單編號": order_no,
                "客戶": str(item.get("H 客戶") or ""),
                "發票對象": str(st.session_state.get("invoice_center_buyer_type") or "自然人"),
                "發票方式": str(st.session_state.get("invoice_center_delivery_method") or "會員載具"),
                "公司抬頭": str(st.session_state.get("invoice_center_company_title") or ""),
                "統編": str(st.session_state.get("invoice_center_buyer_identifier") or ""),
                "載具/捐贈碼": _carrier_value(),
                "API開立類型": str(st.session_state.get("invoice_center_invoice_type") or "一般發票"),
            }
            preview = _build_preview(area_key, order_no, review)
            prepared.append(review)
            previews[order_no] = preview

        if not prepared:
            raise ValueError("沒有可準備的發票資料")

        st.session_state["invoice_pending_queue"] = queue
        st.session_state["invoice_pending_area_key"] = area_key
        st.session_state["invoice_pending_area_label"] = area_label
        st.session_state["invoice_review_rows"] = prepared
        st.session_state["invoice_review_previews"] = previews
        st.session_state["invoice_review_ready"] = True
        st.session_state["invoice_review_area"] = area_label

    def _refresh_previews(rows: list[dict[str, Any]], area_key: str) -> dict[str, dict[str, Any]]:
        previews: dict[str, dict[str, Any]] = {}
        for row in rows:
            order_no = str(row.get("訂單編號") or "").strip()
            if order_no:
                previews[order_no] = _build_preview(area_key, order_no, row)
        st.session_state["invoice_review_rows"] = rows
        st.session_state["invoice_review_previews"] = previews
        return previews

    def _dispatch(rows: list[dict[str, Any]], area_label: str, area_key: str) -> None:
        previews = _refresh_previews(rows, area_key)
        created_by = st.session_state.get("username", "Tool System")
        sent = 0
        for row in rows:
            order_no = str(row.get("訂單編號") or "").strip()
            preview = previews.get(order_no)
            if not order_no or not preview:
                continue
            enqueue_payload(
                area_label,
                order_no,
                json.dumps(preview, ensure_ascii=False),
                created_by=created_by,
            )
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

    def _render_review(area_label: str, area_key: str) -> None:
        if not st.session_state.get("invoice_review_ready"):
            return
        if st.session_state.get("invoice_review_area") != area_label:
            return

        st.divider()
        st.markdown("### 🧾 發票查詢與預覽 Payload（執行後確認）")
        st.caption("可先確認／修改紙本、載具、公司統編與抬頭，再送往鯨躍。")

        review_rows = list(st.session_state.get("invoice_review_rows") or [])
        edited = st.data_editor(
            review_rows,
            hide_index=True,
            use_container_width=True,
            key="invoice_review_editor",
            column_config={
                "訂單編號": st.column_config.TextColumn("訂單編號", disabled=True),
                "客戶": st.column_config.TextColumn("客戶", disabled=True),
                "發票對象": st.column_config.SelectboxColumn("發票對象", options=BUYER_OPTIONS, required=True),
                "發票方式": st.column_config.SelectboxColumn("發票方式", options=DELIVERY_OPTIONS, required=True),
                "公司抬頭": st.column_config.TextColumn("公司抬頭"),
                "統編": st.column_config.TextColumn("統編"),
                "載具/捐贈碼": st.column_config.TextColumn("載具/捐贈碼"),
                "API開立類型": st.column_config.SelectboxColumn("API開立類型", options=list(ui.INVOICE_TYPE_OPTIONS.keys()), required=True),
            },
        )
        rows = _records(edited)

        company_rows = [row for row in rows if row.get("發票對象") == "公司"]
        missing_company = [row.get("訂單編號") for row in company_rows if not str(row.get("統編") or "").strip()]
        if missing_company:
            st.warning("公司發票尚缺統編：" + "、".join(str(x) for x in missing_company))

        cols = st.columns([1, 1])
        with cols[0]:
            if st.button("🔄 套用修改並更新 Payload", use_container_width=True):
                try:
                    with st.spinner("重新產生 Payload…"):
                        _refresh_previews(rows, area_key)
                    st.success("Payload 已更新")
                    st.rerun()
                except Exception as exc:
                    st.error(f"更新 Payload 失敗：{exc}")
        with cols[1]:
            if st.button("▶ 開始開立發票", type="primary", use_container_width=True, disabled=bool(missing_company)):
                try:
                    with st.status("正在建立 Payload 並送往本機 Agent…", expanded=True) as status:
                        _dispatch(rows, area_label, area_key)
                        status.update(label="已送出，等待本機 Agent 處理鯨躍", state="complete")
                    st.success("已送往本機 Agent")
                except Exception as exc:
                    st.error(f"送出失敗：{exc}")

        previews = st.session_state.get("invoice_review_previews") or {}
        if rows:
            current_order = str(rows[0].get("訂單編號") or "")
            preview = previews.get(current_order)
            if preview:
                with st.expander(f"預覽 Payload：{current_order}", expanded=True):
                    st.json(preview)

    def render_invoice_create() -> None:
        options = ui.get_area_options()
        labels = [display for _key, display in options]
        key_by_label = {display: key for key, display in options}

        st.markdown("### 📍 執行區域")
        area_label = st.selectbox(
            "執行區域",
            labels,
            label_visibility="collapsed",
            key="invoice_pending_picker_area",
        )
        area_key = key_by_label[area_label]
        st.caption(f"只執行：{area_label}")

        try:
            candidates = get_pending_invoice_candidates(area_label)
            select_all = st.checkbox("全選", key=f"invoice_pending_select_all_{area_label}")
            visible_keys = ["選取", *DISPLAY_COLUMNS]
            visible_rows = []
            for row in candidates:
                item = {key: row.get(key, "") for key in visible_keys}
                if select_all:
                    item["選取"] = True
                visible_rows.append(item)

            editor = st.data_editor(
                visible_rows,
                hide_index=True,
                use_container_width=True,
                disabled=list(DISPLAY_COLUMNS),
                column_config={"選取": st.column_config.CheckboxColumn("執行", default=False)},
                key=f"invoice_pending_picker_{area_label}_{int(select_all)}",
            )
            selected = [row for row in _records(editor) if row.get("選取")]
            by_row = {row["列號"]: row for row in candidates}
            queue = [by_row[row["列號"]] for row in selected if row.get("列號") in by_row]
            st.caption(f"待開立發票：{len(candidates)} 筆；已勾選：{len(queue)} 筆")
        except Exception as exc:
            queue = []
            st.error(f"讀取清潔異動表失敗：{exc}")

        # 第一段執行：只查詢訂單並準備 Payload，尚不送 Agent。
        if st.button("▶ 執行", type="primary", use_container_width=True, disabled=not queue):
            try:
                with st.status("已接收執行需求，正在查詢訂單與發票設定…", expanded=True) as status:
                    st.write(f"地區：{area_label}；勾選：{len(queue)} 筆")
                    _prepare_invoice(area_label, area_key, queue)
                    status.update(label="查詢完成，請在下方確認發票設定與 Payload", state="complete")
                st.rerun()
            except Exception as exc:
                st.error(f"發票資料準備失敗：{exc}")

        # 保留上方「區域／清單／執行」，執行後才在其下方出現新版確認區。
        _render_review(area_label, area_key)

    ui.render_invoice_create = render_invoice_create
