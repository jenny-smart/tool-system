from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from .bridge import create_invoice_from_payload, fetch_backend_order_invoice_payload
from .config import get_area_options, get_area_status
from .invoice import build_invoice_payload
from .lemon_invoice_api import INVOICE_TYPE_OPTIONS, make_invoice
from .models import InvoiceLineItem, to_decimal
from .query import query_invoice_by_order_id

DEFAULT_PRODUCTS = [
    {"品號": "SV001", "品名": "清潔服務-平日(2人)", "單位": "1小時", "單價": 1200, "稅率": "5%", "狀態": "啟用"},
    {"品號": "SV002", "品名": "清潔服務-週末(2人)", "單位": "1小時", "單價": 1400, "稅率": "5%", "狀態": "啟用"},
    {"品號": "SV003", "品名": "清潔服務-週末加價(1人)", "單位": "1小時", "單價": 100, "稅率": "5%", "狀態": "啟用"},
    {"品號": "GEN001", "品名": "異動費用-一般(2人)", "單位": "1小時", "單價": 360, "稅率": "5%", "狀態": "啟用"},
    {"品號": "VIP001", "品名": "異動費用-VIP", "單位": "1", "單價": 600, "稅率": "5%", "狀態": "啟用"},
    {"品號": "CAR001", "品名": "車馬費(1人)", "單位": "人", "單價": 100, "稅率": "5%", "狀態": "啟用"},
    {"品號": "TOOL001", "品名": "工具組(組)", "單位": "組", "單價": 700, "稅率": "5%", "狀態": "啟用"},
]


def _area_select(label: str = "地區") -> str:
    options = get_area_options()
    labels = [item[1] for item in options]
    selected = st.selectbox(label, labels, index=0)
    return dict((display, key) for key, display in options)[selected]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(to_decimal(value))
    except Exception:
        return default


def _as_date(value: Any) -> date:
    text = str(value or "").strip()
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return date.today()


def _set_default(key: str, value: Any) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _order_extra_value(order: dict[str, Any], *keys: str) -> str:
    extra = order.get("extra") or {}
    for key in keys:
        value = order.get(key)
        if value not in (None, ""):
            return str(value)
        if isinstance(extra, dict):
            value = extra.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _line_amount(row: dict[str, Any]) -> int:
    return _as_int(row.get("數量"), 1) * _as_int(row.get("單價"), 0)


def _blank_line_item() -> dict[str, Any]:
    return {"品號": "", "品名": "", "單位": "式", "數量": 1, "單價": 0, "金額": 0, "備註": ""}


def _normalize_line_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        item = {
            "品號": str(row.get("品號") or "").strip(),
            "品名": str(row.get("品名") or "").strip(),
            "單位": str(row.get("單位") or "式").strip(),
            "數量": max(_as_int(row.get("數量"), 1), 1),
            "單價": max(_as_int(row.get("單價"), 0), 0),
            "備註": str(row.get("備註") or "").strip(),
        }
        item["金額"] = _line_amount(item)
        if any([item["品號"], item["品名"], item["單價"], item["備註"]]):
            normalized.append(item)
    return normalized or [_blank_line_item()]


def _ensure_product_catalog() -> None:
    if "invoice_center_product_catalog" not in st.session_state:
        st.session_state["invoice_center_product_catalog"] = [dict(item) for item in DEFAULT_PRODUCTS]


def _ensure_line_items() -> None:
    if "invoice_center_line_items" not in st.session_state:
        st.session_state["invoice_center_line_items"] = [
            {"品號": "SV001", "品名": "清潔服務-平日(2人)", "單位": "1小時", "數量": 1, "單價": 1200, "金額": 1200, "備註": ""}
        ]


def _apply_payload_to_state(payload: Any) -> None:
    item = payload.items[0] if payload.items else InvoiceLineItem(
        goodcode="CLEAN",
        goodname="清潔服務",
        unit="式",
        quantity="1",
        unitprice="0",
        amount="0",
    )
    st.session_state.update(
        {
            "invoice_center_orderdate": _as_date(payload.orderdate),
            "invoice_center_saleamount": _as_int(payload.saleamount),
            "invoice_center_payway": payload.payway or "",
            "invoice_center_buyer_identifier": payload.buyer_identifier or "",
            "invoice_center_buyer_name": payload.buyer_name or "",
            "invoice_center_buyer_phone": payload.buyer_phone or "",
            "invoice_center_buyer_emailaddress": payload.buyer_emailaddress or "",
            "invoice_center_buyer_address": payload.buyer_address or "",
            "invoice_center_mainremark": payload.mainremark or "",
            "invoice_center_goodcode": item.goodcode or "CLEAN",
            "invoice_center_goodname": item.goodname or "清潔服務",
            "invoice_center_unit": item.unit or "式",
            "invoice_center_quantity": max(_as_int(item.quantity, 1), 1),
            "invoice_center_unitprice": _as_int(item.unitprice),
            "invoice_center_fremark": item.fremark or "",
        }
    )
    if item:
        st.session_state["invoice_center_line_items"] = _normalize_line_items(
            [
                {
                    "品號": item.goodcode or "CLEAN",
                    "品名": item.goodname or "清潔服務",
                    "單位": item.unit or "式",
                    "數量": max(_as_int(item.quantity, 1), 1),
                    "單價": _as_int(item.unitprice),
                    "備註": item.fremark or "",
                }
            ]
        )


def _bootstrap_invoice_state() -> None:
    defaults = {
        "invoice_center_orderdate": date.today(),
        "invoice_center_saleamount": 0,
        "invoice_center_payway": "現金",
        "invoice_center_buyer_identifier": "",
        "invoice_center_buyer_name": "",
        "invoice_center_buyer_phone": "",
        "invoice_center_buyer_emailaddress": "",
        "invoice_center_buyer_address": "",
        "invoice_center_mainremark": "",
        "invoice_center_invoice_note": "",
        "invoice_center_buyer_no": "",
        "invoice_center_invoice_type": "一般發票",
        "invoice_center_ei_invoice_kind": "一般稅額電子發票",
        "invoice_center_tax_status": "應稅",
        "invoice_center_has_tax": "含稅",
        "invoice_center_tax_rate": 0.05,
        "invoice_center_bind_report": "綁定",
        "invoice_center_invoice_way": "載具",
        "invoice_center_carrier_type": "手機條碼 / EJ0011",
        "invoice_center_carrier_no": "",
        "invoice_center_hidden_code": "",
        "invoice_center_round_digits": 4,
        "invoice_center_goodcode": "CLEAN",
        "invoice_center_goodname": "清潔服務",
        "invoice_center_unit": "式",
        "invoice_center_quantity": 1,
        "invoice_center_unitprice": 0,
        "invoice_center_fremark": "",
    }
    for key, value in defaults.items():
        _set_default(key, value)
    _ensure_product_catalog()
    _ensure_line_items()


def _render_invoice_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.6rem; max-width: 1180px;}
        [data-testid="stMetricValue"] {font-size: 1.25rem;}
        .invoice-hero {
            padding: 18px 22px; border: 1px solid #d8e5ff; border-radius: 18px;
            background: linear-gradient(135deg, #f7fbff 0%, #ffffff 60%, #f4f8ff 100%);
            margin-bottom: 14px;
        }
        .invoice-hero h2 {margin: 0 0 4px 0; color: #123b7a;}
        .invoice-hero p {margin: 0; color: #5f6b7a;}
        .invoice-card {
            border: 1px solid #dbe6f7; border-radius: 16px; padding: 8px 4px 4px 4px;
            background: #ffffff; box-shadow: 0 8px 24px rgba(33, 71, 128, 0.05);
        }
        .invoice-chip {
            display: inline-block; padding: 4px 10px; border-radius: 999px;
            background: #edf4ff; color: #1d57ad; border: 1px solid #cfe0ff; font-size: 12px;
            margin-right: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _load_backend_order(area: str, order_no: str, suffix: str) -> dict[str, Any]:
    backend_order, loaded_payload = fetch_backend_order_invoice_payload(
        area,
        order_no,
        suffix=suffix,
    )
    order_dict = backend_order.to_dict()
    st.session_state["invoice_center_backend_order"] = order_dict
    _apply_payload_to_state(loaded_payload)
    return order_dict


def _render_backend_order_summary(invoice_type: str) -> None:
    order = st.session_state.get("invoice_center_backend_order")
    if not order:
        st.info("先輸入 Lemon 訂單號並查詢，系統會自動帶入 purchase_id、付款狀態、發票號碼與載具資料。")
        return

    st.success(f"已帶入 Lemon 訂單：{order.get('order_no', '')}")
    summary_cols = st.columns(5)
    summary_cols[0].metric("purchase_id", order.get("purchase_id", "") or "-")
    summary_cols[1].metric("付款狀態", order.get("paid_status", "") or "-")
    summary_cols[2].metric("發票號碼", order.get("invoice_no", "") or "未開立")
    summary_cols[3].metric("發票類型", invoice_type)
    summary_cols[4].metric("付款方式", order.get("payway", "") or "-")
    st.dataframe(
        [
            {
                "訂單編號": order.get("order_no", ""),
                "載具類型": _order_extra_value(order, "carrier_type", "carriertype"),
                "載具號碼": _order_extra_value(order, "carrier_no", "carrierid1", "carrierid2"),
                "統一編號": _order_extra_value(order, "buyer_identifier", "invoice_identifier", "tax_id"),
                "抬頭": _order_extra_value(order, "buyer_name", "invoice_title", "company_title"),
            }
        ],
        use_container_width=True,
        hide_index=True,
    )
    if order.get("items"):
        st.caption("服務項目：" + "、".join(order.get("items", [])))


def _render_lemon_invoice_action(area: str, order_no: str, suffix: str, invoice_type: str) -> None:
    order = st.session_state.get("invoice_center_backend_order")
    if not order:
        st.warning("尚未查詢 Lemon 訂單，無法開立發票。")
        return

    purchase_id = str(order.get("purchase_id") or "").strip()
    invoice_no = str(order.get("invoice_no") or "").strip()

    if invoice_no:
        st.success(f"此訂單已開立發票：{invoice_no}")
        return

    if not purchase_id:
        st.warning("此訂單沒有 purchase_id，無法呼叫 Lemon 開票 API。")
        return

    if st.button("🧾 開立發票（送 Lemon API）", type="primary", use_container_width=True):
        try:
            make_invoice(purchase_id, invoice_type=invoice_type)
            st.info("已送出開立請求")
            refreshed_order = _load_backend_order(area, order_no, suffix)
            refreshed_invoice_no = str(refreshed_order.get("invoice_no") or "").strip()
            if refreshed_invoice_no:
                st.success(f"開立成功：{refreshed_invoice_no}")
            else:
                st.warning("已送出，但尚未查到發票號碼，請稍後重新查詢。")
        except Exception as exc:
            st.error(str(exc))


def _render_product_picker() -> None:
    _ensure_product_catalog()
    products = [p for p in st.session_state["invoice_center_product_catalog"] if p.get("狀態") == "啟用"]
    st.markdown("#### 商品查詢 / 快速帶入")
    keyword = st.text_input("搜尋商品", placeholder="輸入品號或品名，例如 VIP、清潔、車馬費", key="invoice_product_search")
    if keyword:
        key = keyword.lower()
        products = [p for p in products if key in str(p.get("品號", "")).lower() or key in str(p.get("品名", "")).lower()]

    if not products:
        st.info("查無符合商品，可到下方商品管理新增。")
        return

    selected_label = st.selectbox(
        "選擇商品",
        [f"{p['品號']}｜{p['品名']}｜{p['單位']}｜{p['單價']:,}" for p in products],
        key="invoice_selected_product_label",
    )
    selected_index = [f"{p['品號']}｜{p['品名']}｜{p['單位']}｜{p['單價']:,}" for p in products].index(selected_label)
    product = products[selected_index]

    qty_col, note_col, add_col = st.columns([1, 2, 1])
    with qty_col:
        qty = st.number_input("帶入數量", min_value=1, step=1, value=1, key="invoice_product_qty")
    with note_col:
        note = st.text_input("明細備註", key="invoice_product_note")
    with add_col:
        st.write("")
        st.write("")
        if st.button("➕ 加入明細", use_container_width=True):
            rows = _normalize_line_items(st.session_state.get("invoice_center_line_items", []))
            if len(rows) == 1 and not rows[0].get("品號") and not rows[0].get("品名"):
                rows = []
            rows.append(
                {
                    "品號": product.get("品號", ""),
                    "品名": product.get("品名", ""),
                    "單位": product.get("單位", "式"),
                    "數量": qty,
                    "單價": _as_int(product.get("單價"), 0),
                    "金額": qty * _as_int(product.get("單價"), 0),
                    "備註": note,
                }
            )
            st.session_state["invoice_center_line_items"] = rows
            st.success("已加入發票明細")
            st.rerun()


def _render_line_items_editor() -> list[dict[str, Any]]:
    _ensure_line_items()
    st.markdown("#### 發票明細")
    edited = st.data_editor(
        st.session_state["invoice_center_line_items"],
        key="invoice_center_line_items_editor",
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "品號": st.column_config.TextColumn("品號", required=False),
            "品名": st.column_config.TextColumn("品名", required=True),
            "單位": st.column_config.TextColumn("單位", required=True),
            "數量": st.column_config.NumberColumn("數量", min_value=1, step=1, required=True),
            "單價": st.column_config.NumberColumn("單價", min_value=0, step=1, required=True),
            "金額": st.column_config.NumberColumn("金額", disabled=True),
            "備註": st.column_config.TextColumn("備註", required=False),
        },
    )
    rows = _normalize_line_items(edited)
    st.session_state["invoice_center_line_items"] = rows
    return rows


def _render_product_manager() -> None:
    _ensure_product_catalog()
    with st.expander("商品管理：新增 / 修改 / 刪除", expanded=False):
        st.caption("此商品表可在本次操作中新增、修改、刪除；重新部署後會回到預設商品。若要永久商品庫，下一版可接 Google Sheet。")
        edited_products = st.data_editor(
            st.session_state["invoice_center_product_catalog"],
            key="invoice_center_product_catalog_editor",
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "品號": st.column_config.TextColumn("品號", required=True),
                "品名": st.column_config.TextColumn("品名", required=True),
                "單位": st.column_config.TextColumn("單位", required=True),
                "單價": st.column_config.NumberColumn("單價", min_value=0, step=1, required=True),
                "稅率": st.column_config.SelectboxColumn("稅率", options=["5%", "0%", "免稅"], required=True),
                "狀態": st.column_config.SelectboxColumn("狀態", options=["啟用", "停用", "刪除"], required=True),
            },
        )
        actions = st.columns([1, 1, 3])
        if actions[0].button("💾 套用商品表", use_container_width=True):
            st.session_state["invoice_center_product_catalog"] = [
                dict(p) for p in edited_products if p and p.get("品號") and p.get("狀態") != "刪除"
            ]
            st.success("商品表已套用")
            st.rerun()
        if actions[1].button("↩️ 重設預設商品", use_container_width=True):
            st.session_state["invoice_center_product_catalog"] = [dict(item) for item in DEFAULT_PRODUCTS]
            st.rerun()


def _build_payload(area: str, order_no: str, suffix: str, orderdate: date, saleamount: int, payway: str, rows: list[dict[str, Any]]) -> Any:
    invoice_items = [
        InvoiceLineItem(
            goodcode=str(row.get("品號", "")),
            goodname=str(row.get("品名", "")),
            unit=str(row.get("單位", "式")),
            quantity=str(row.get("數量", 1)),
            unitprice=str(row.get("單價", 0)),
            amount=str(row.get("金額", _line_amount(row))),
            fremark=str(row.get("備註", "")),
        )
        for row in rows
        if row.get("品名")
    ]
    return build_invoice_payload(
        area=area,
        order_no=order_no,
        suffix=suffix,
        orderdate=orderdate.isoformat(),
        saleamount=saleamount,
        buyer_identifier=st.session_state.get("invoice_center_buyer_identifier", ""),
        buyer_name=st.session_state.get("invoice_center_buyer_name", ""),
        buyer_address=st.session_state.get("invoice_center_buyer_address", ""),
        buyer_emailaddress=st.session_state.get("invoice_center_buyer_emailaddress", ""),
        buyer_phone=st.session_state.get("invoice_center_buyer_phone", ""),
        payway=payway,
        mainremark=st.session_state.get("invoice_center_mainremark", ""),
        items=invoice_items,
    )


def _render_invoice_create_tab() -> None:
    _render_invoice_css()
    _bootstrap_invoice_state()

    st.markdown(
        """
        <div class="invoice-hero">
          <h2>發票開立</h2>
          <p><span class="invoice-chip">新版 UI</span><span class="invoice-chip">Lemon API</span><span class="invoice-chip">商品管理</span> 查詢訂單後以 purchase_id 開立，不再登入 Cetustek / EI。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown("### 一、訂單資訊")
        col_area, col_order, col_suffix, col_query = st.columns([1, 2, 1, 1])
        with col_area:
            area = _area_select()
        with col_order:
            order_no = st.text_input("Lemon 訂單號", placeholder="LC00212058")
        with col_suffix:
            suffix = st.text_input("EI orderid suffix", value="-1")
        with col_query:
            st.write("")
            st.write("")
            if st.button("🔎 查詢 Lemon 訂單", type="primary", use_container_width=True):
                try:
                    _load_backend_order(area, order_no, suffix)
                except Exception as exc:
                    st.session_state.pop("invoice_center_backend_order", None)
                    st.error(f"Lemon 訂單查詢失敗：{exc}")

        invoice_type = st.selectbox(
            "Lemon 開立類型",
            list(INVOICE_TYPE_OPTIONS.keys()),
            key="invoice_center_invoice_type",
        )
        _render_backend_order_summary(invoice_type)

    left, right = st.columns([2.2, 1])
    with left:
        with st.container(border=True):
            st.markdown("### 二、發票基本資料")
            col_date, col_order_date, col_phone = st.columns(3)
            with col_date:
                orderdate = st.date_input("發票日期 *", key="invoice_center_orderdate")
            with col_order_date:
                st.date_input("訂單日期", key="invoice_center_orderdate_mirror", value=st.session_state["invoice_center_orderdate"], disabled=True)
            with col_phone:
                st.text_input("買方電話", key="invoice_center_buyer_phone")

            col_name, col_identifier, col_email = st.columns(3)
            with col_name:
                st.text_input("買方名稱 *", key="invoice_center_buyer_name")
            with col_identifier:
                st.text_input("買方統編", key="invoice_center_buyer_identifier")
            with col_email:
                st.text_input("買方 Email", key="invoice_center_buyer_emailaddress")

            st.text_input("買方地址", key="invoice_center_buyer_address")

            col_payway, col_ei_kind, col_bind = st.columns(3)
            with col_payway:
                payway = st.selectbox("付款方式 *", ["現金", "信用卡", "ATM", "轉帳", "LINE Pay", "其他"], key="invoice_center_payway")
            with col_ei_kind:
                st.selectbox("發票類型 *", ["一般稅額電子發票", "特種稅額電子發票"], key="invoice_center_ei_invoice_kind")
            with col_bind:
                st.radio("訂單日期綁定申報期", ["綁定", "不綁定"], horizontal=True, key="invoice_center_bind_report")

        with st.container(border=True):
            st.markdown("### 三、發票設定")
            col_has_tax, col_tax_status, col_rate = st.columns(3)
            with col_has_tax:
                st.radio("單價是否含稅", ["未稅", "含稅"], horizontal=True, key="invoice_center_has_tax")
            with col_tax_status:
                st.radio("營業稅", ["應稅", "零稅率", "免稅", "應稅(特種稅率)"], horizontal=True, key="invoice_center_tax_status")
            with col_rate:
                st.number_input("稅率", min_value=0.0, max_value=1.0, step=0.01, format="%.2f", key="invoice_center_tax_rate")

            col_way, col_carrier_type, col_carrier_no = st.columns([1, 1.5, 1.5])
            with col_way:
                st.radio("發票方式", ["載具", "捐贈", "紙本"], horizontal=True, key="invoice_center_invoice_way")
            with col_carrier_type:
                st.selectbox("載具類別/編號", ["手機條碼 / EJ0011", "自然人憑證", "會員載具", "無"], key="invoice_center_carrier_type")
            with col_carrier_no:
                st.text_input("載具號碼", key="invoice_center_carrier_no")

            col_hidden, col_round = st.columns([2, 1])
            with col_hidden:
                st.text_input("隱碼 / 愛心碼", key="invoice_center_hidden_code")
            with col_round:
                st.radio("發票計算位數", [0, 1, 2, 3, 4, 5, 6, 7], horizontal=True, key="invoice_center_round_digits")

            note_cols = st.columns(2)
            with note_cols[0]:
                st.text_area("買方備註", key="invoice_center_mainremark", height=90)
            with note_cols[1]:
                st.text_area("發票備註", key="invoice_center_invoice_note", height=90)

        with st.container(border=True):
            st.markdown("### 四、商品明細")
            _render_product_picker()
            rows = _render_line_items_editor()
            _render_product_manager()

    subtotal = sum(_as_int(row.get("金額"), 0) for row in st.session_state.get("invoice_center_line_items", []))
    tax_rate = float(st.session_state.get("invoice_center_tax_rate", 0.05) or 0)
    tax_amount = 0 if st.session_state.get("invoice_center_tax_status") in ["免稅", "零稅率"] else round(subtotal * tax_rate)
    total = subtotal + tax_amount
    saleamount_value = subtotal or st.session_state.get("invoice_center_saleamount", 0)

    with right:
        with st.container(border=True):
            st.markdown("### 摘要")
            st.metric("商品合計", f"{subtotal:,}")
            st.metric("營業稅", f"{tax_amount:,}")
            st.metric("總金額", f"{total:,}")
            st.caption(f"開立類型：{invoice_type}")
            st.caption(f"付款方式：{payway}")
            st.caption("實際開立仍以 Lemon API 依 purchase_id 處理；畫面欄位供檢查與 payload 預覽。")

        with st.container(border=True):
            st.markdown("### 操作")
            payload = _build_payload(area, order_no, suffix, orderdate, saleamount_value, payway, st.session_state.get("invoice_center_line_items", []))
            if st.button("🔍 預覽 Payload", use_container_width=True):
                result = create_invoice_from_payload(payload, dry_run=True)
                st.session_state["invoice_center_preview"] = result.payload
            _render_lemon_invoice_action(area, order_no, suffix, invoice_type)
            if st.button("🔄 重新查詢", use_container_width=True):
                try:
                    _load_backend_order(area, order_no, suffix)
                    st.rerun()
                except Exception as exc:
                    st.error(f"重新查詢失敗：{exc}")

    preview = st.session_state.get("invoice_center_preview")
    if preview:
        with st.expander("Payload 預覽", expanded=False):
            st.json(preview)


def _render_allowance_tab() -> None:
    st.subheader("折讓單")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.text_input("原發票號碼")
    with col2:
        st.date_input("折讓日期", value=date.today())
    with col3:
        st.number_input("折讓金額", min_value=0, step=1, value=0)
    st.info("折讓 API 尚待補齊；目前先保留輸入骨架。")


def _render_download_tab() -> None:
    st.subheader("發票下載")
    col1, col2, col3, col4 = st.columns([1, 2, 1, 1])
    with col1:
        area = _area_select("查詢地區")
    with col2:
        order_id = st.text_input("EI orderid", placeholder="LC00212058-1")
    with col3:
        date1 = st.date_input("起日", value=date.today().replace(day=1))
    with col4:
        date2 = st.date_input("迄日", value=date.today())
    captcha = st.text_input("Captcha", value="", type="password", key="invoice_query_captcha")

    if st.button("查詢發票號碼", use_container_width=True):
        try:
            rows = query_invoice_by_order_id(
                order_id,
                date1.isoformat(),
                date2.isoformat(),
                area=area,
                captcha=captcha or None,
            )
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("查無資料")
        except Exception as exc:
            st.error(f"查詢失敗：{exc}")

    st.info("下載 PDF/XML API 尚待補齊；目前先提供發票號碼查詢。")


def _render_settings_tab() -> None:
    st.subheader("設定")
    rows = get_area_status()
    display_rows = [
        {
            "地區": row["label"],
            "帳號環境變數": row["userid_env"],
            "密碼環境變數": row["password_env"],
            "帳號已設定": row["has_userid"],
            "密碼已設定": row["has_password"],
            "可使用": row["configured"],
        }
        for row in rows
    ]
    st.dataframe(display_rows, use_container_width=True, hide_index=True)
    st.caption("此頁只顯示設定狀態，不顯示帳號、密碼、cookie 或 token。")


def render_invoice_center() -> None:
    st.header("發票中心")
    tabs = st.tabs(["發票開立", "折讓單", "發票下載", "設定"])
    with tabs[0]:
        _render_invoice_create_tab()
    with tabs[1]:
        _render_allowance_tab()
    with tabs[2]:
        _render_download_tab()
    with tabs[3]:
        _render_settings_tab()
