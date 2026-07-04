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
    {"品號": "CLEAN-2P-WD", "品名": "清潔服務 平日(2人)", "單位": "1小時", "單價": 1200, "稅率": "5%", "狀態": "啟用"},
    {"品號": "CLEAN-2P-WE", "品名": "清潔服務 週末(2人)", "單位": "1小時", "單價": 1400, "稅率": "5%", "狀態": "啟用"},
    {"品號": "CLEAN-1P-UP", "品名": "清潔服務 週末加價(1人)", "單位": "1小時", "單價": 100, "稅率": "5%", "狀態": "啟用"},
    {"品號": "MOVE-FEE", "品名": "異動費", "單位": "1次", "單價": 360, "稅率": "5%", "狀態": "啟用"},
    {"品號": "VIP-MOVE", "品名": "異動費用-VIP", "單位": "1", "單價": 600, "稅率": "5%", "狀態": "啟用"},
    {"品號": "TRAVEL", "品名": "車馬費(1人)", "單位": "人", "單價": 100, "稅率": "5%", "狀態": "啟用"},
    {"品號": "TOOL-SET", "品名": "工具組", "單位": "1組", "單價": 700, "稅率": "5%", "狀態": "啟用"},
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
    return {"序號": 1, "商品代碼": "", "商品名稱": "", "單位": "1", "數量": 1, "單價": 0, "金額": 0, "備註": ""}


def _normalize_line_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not row:
            continue
        item = {
            "序號": index,
            "商品代碼": str(row.get("商品代碼") or row.get("品號") or "").strip(),
            "商品名稱": str(row.get("商品名稱") or row.get("品名") or "").strip(),
            "單位": str(row.get("單位") or "1").strip(),
            "數量": max(_as_int(row.get("數量"), 1), 1),
            "單價": max(_as_int(row.get("單價"), 0), 0),
            "備註": str(row.get("備註") or "").strip(),
        }
        item["金額"] = _line_amount({"數量": item["數量"], "單價": item["單價"]})
        if any([item["商品代碼"], item["商品名稱"], item["單價"], item["備註"]]):
            normalized.append(item)
    for index, item in enumerate(normalized, start=1):
        item["序號"] = index
    return normalized or [_blank_line_item()]


def _ensure_product_catalog() -> None:
    if "invoice_center_product_catalog" not in st.session_state:
        st.session_state["invoice_center_product_catalog"] = [dict(item) for item in DEFAULT_PRODUCTS]


def _ensure_line_items() -> None:
    if "invoice_center_line_items" not in st.session_state:
        st.session_state["invoice_center_line_items"] = [
            {"序號": 1, "商品代碼": "CLEAN-2P-WD", "商品名稱": "清潔服務 平日(2人)", "單位": "1小時", "數量": 4, "單價": 1200, "金額": 4800, "備註": ""},
            {"序號": 2, "商品代碼": "MOVE-FEE", "商品名稱": "異動費", "單位": "1次", "數量": 1, "單價": 360, "金額": 360, "備註": ""},
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
            "invoice_center_payway": payload.payway or st.session_state.get("invoice_center_payway", "現金"),
            "invoice_center_buyer_identifier": payload.buyer_identifier or "",
            "invoice_center_buyer_name": payload.buyer_name or "",
            "invoice_center_buyer_phone": payload.buyer_phone or "",
            "invoice_center_buyer_emailaddress": payload.buyer_emailaddress or "",
            "invoice_center_buyer_address": payload.buyer_address or "",
            "invoice_center_mainremark": payload.mainremark or "",
        }
    )
    if item:
        st.session_state["invoice_center_line_items"] = _normalize_line_items(
            [
                {
                    "商品代碼": item.goodcode or "CLEAN",
                    "商品名稱": item.goodname or "清潔服務",
                    "單位": item.unit or "式",
                    "數量": max(_as_int(item.quantity, 1), 1),
                    "單價": _as_int(item.unitprice),
                    "備註": item.fremark or "",
                }
            ]
        )


def _apply_order_invoice_defaults(order: dict[str, Any]) -> None:
    buyer_identifier = _order_extra_value(order, "buyer_identifier", "invoice_identifier", "tax_id", "company_no") or str(order.get("buyer_identifier") or "")
    company_title = _order_extra_value(order, "buyer_name", "invoice_title", "company_title") or str(order.get("buyer_name") or "")
    carrier_no = _order_extra_value(order, "carrier_no", "carrierid1", "carrierid2", "carrier_info")
    carrier_type = _order_extra_value(order, "carrier_type", "carriertype", "carrier_label")
    donate_code = _order_extra_value(order, "donate_code", "love_code", "npoban")

    if buyer_identifier:
        st.session_state["invoice_center_buyer_type"] = "公司"
        st.session_state["invoice_center_invoice_kind"] = "三聯式"
        st.session_state["invoice_center_buyer_identifier"] = buyer_identifier
        if company_title:
            st.session_state["invoice_center_company_title"] = company_title
            st.session_state["invoice_center_buyer_name"] = company_title
    else:
        st.session_state["invoice_center_buyer_type"] = "自然人"
        st.session_state["invoice_center_invoice_kind"] = "二聯式"

    if donate_code:
        st.session_state["invoice_center_delivery_method"] = "捐贈"
        st.session_state["invoice_center_donate_code"] = donate_code
    elif carrier_no or "手機" in carrier_type:
        st.session_state["invoice_center_delivery_method"] = "手機載具"
        st.session_state["invoice_center_mobile_barcode"] = carrier_no
    elif "自然人" in carrier_type:
        st.session_state["invoice_center_delivery_method"] = "自然人憑證"
        st.session_state["invoice_center_citizen_cert"] = carrier_no
    elif "會員" in carrier_type:
        st.session_state["invoice_center_delivery_method"] = "會員載具"
        st.session_state["invoice_center_member_carrier"] = carrier_no or str(order.get("email") or "")


def _bootstrap_invoice_state() -> None:
    defaults = {
        "invoice_center_orderdate": date.today(),
        "invoice_center_saleamount": 0,
        "invoice_center_payway": "現金",
        "invoice_center_buyer_type": "自然人",
        "invoice_center_invoice_kind": "二聯式",
        "invoice_center_buyer_identifier": "",
        "invoice_center_company_title": "",
        "invoice_center_buyer_name": "",
        "invoice_center_buyer_phone": "",
        "invoice_center_buyer_emailaddress": "",
        "invoice_center_buyer_address": "",
        "invoice_center_mainremark": "",
        "invoice_center_invoice_note": "",
        "invoice_center_tax_mode": "單價含稅",
        "invoice_center_tax_status": "應稅",
        "invoice_center_tax_rate": 0.05,
        "invoice_center_delivery_method": "會員載具",
        "invoice_center_member_carrier": "",
        "invoice_center_mobile_barcode": "",
        "invoice_center_citizen_cert": "",
        "invoice_center_donate_code": "",
        "invoice_center_invoice_type": "一般發票",
    }
    for key, value in defaults.items():
        _set_default(key, value)
    _ensure_product_catalog()
    _ensure_line_items()


def _render_invoice_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.35rem; max-width: 1260px;}
        [data-testid="stMetricValue"] {font-size: 1.3rem;}
        .invoice-hero {
            padding: 18px 22px; border: 1px solid #d8e7ff; border-radius: 18px;
            background: linear-gradient(135deg, #f7fbff 0%, #ffffff 62%, #eef6ff 100%);
            margin-bottom: 14px; box-shadow: 0 10px 26px rgba(31, 83, 155, 0.06);
        }
        .invoice-hero h2 {margin: 0 0 5px 0; color: #0f376f; font-size: 28px;}
        .invoice-hero p {margin: 0; color: #5b6778;}
        .invoice-chip {
            display: inline-block; padding: 4px 10px; border-radius: 999px;
            background: #edf5ff; color: #1557ad; border: 1px solid #cce0ff; font-size: 12px;
            margin-right: 6px; font-weight: 600;
        }
        .section-title {font-size: 18px; font-weight: 800; color: #1c2b3a; margin-bottom: 6px;}
        .mini-note {color:#667085; font-size: 13px;}
        .status-ok {color:#16824b; font-weight:700;}
        .status-warn {color:#b54708; font-weight:700;}
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
    _apply_order_invoice_defaults(order_dict)
    return order_dict


def _render_top_query() -> tuple[str, str, str, str]:
    with st.container(border=True):
        query_cols = st.columns([1.3, 2.1, 1, 1, 1])
        with query_cols[0]:
            area = _area_select()
        with query_cols[1]:
            order_no = st.text_input("Lemon 訂單號", placeholder="LC00212058")
        with query_cols[2]:
            suffix = st.text_input("進階編號 suffix", value="-1")
        with query_cols[3]:
            invoice_type = st.selectbox("Lemon API 類型", list(INVOICE_TYPE_OPTIONS.keys()), key="invoice_center_invoice_type")
        with query_cols[4]:
            st.write("")
            st.write("")
            if st.button("🔎 查詢訂單", type="primary", use_container_width=True):
                try:
                    _load_backend_order(area, order_no, suffix)
                except Exception as exc:
                    st.session_state.pop("invoice_center_backend_order", None)
                    st.error(f"Lemon 訂單查詢失敗：{exc}")
    return area, order_no, suffix, invoice_type


def _render_order_overview() -> None:
    order = st.session_state.get("invoice_center_backend_order") or {}
    with st.container(border=True):
        st.markdown('<div class="section-title">1 訂單資訊</div>', unsafe_allow_html=True)
        cols = st.columns(6)
        cols[0].metric("訂單狀態", order.get("order_status", "-") or "-")
        cols[1].metric("purchase_id", order.get("purchase_id", "-") or "-")
        cols[2].metric("發票狀態", "已開立" if order.get("invoice_no") else "未開立")
        cols[3].metric("發票號碼", order.get("invoice_no", "-") or "-")
        cols[4].metric("是否作廢", "否")
        cols[5].metric("付款方式", order.get("payway", st.session_state.get("invoice_center_payway", "-")) or "-")
        detail_cols = st.columns(4)
        detail_cols[0].text_input("地區", value="", disabled=True, placeholder="查詢後依上方地區")
        detail_cols[1].text_input("Lemon 訂單號", value=order.get("order_no", ""), disabled=True)
        detail_cols[2].date_input("訂單日期", key="invoice_center_orderdate")
        detail_cols[3].selectbox("付款方式", ["現金", "信用卡", "ATM", "轉帳", "LINE Pay", "其他"], key="invoice_center_payway")


def _render_buyer_section() -> None:
    with st.container(border=True):
        st.markdown('<div class="section-title">2 買受人資料</div>', unsafe_allow_html=True)
        cols = st.columns([1, 1, 1])
        with cols[0]:
            buyer_type = st.radio("買受人類型", ["自然人", "公司"], horizontal=True, key="invoice_center_buyer_type")
        with cols[1]:
            forced_kind = "三聯式" if buyer_type == "公司" else "二聯式"
            st.session_state["invoice_center_invoice_kind"] = forced_kind
            st.radio("發票種類", [forced_kind], horizontal=True, key="invoice_kind_locked")
        with cols[2]:
            st.selectbox("課稅別", ["應稅", "零稅率", "免稅"], key="invoice_center_tax_status")

        if buyer_type == "公司":
            company_cols = st.columns([2, 1])
            with company_cols[0]:
                company_title = st.text_input("公司抬頭 / 買方名稱 *", key="invoice_center_company_title")
                if company_title:
                    st.session_state["invoice_center_buyer_name"] = company_title
            with company_cols[1]:
                st.text_input("統一編號 *", key="invoice_center_buyer_identifier")
        else:
            st.text_input("買方名稱 / 會員姓名 *", key="invoice_center_buyer_name")

        address_col, phone_col = st.columns([2, 1])
        with address_col:
            st.text_input("地址", key="invoice_center_buyer_address")
        with phone_col:
            st.text_input("電話", key="invoice_center_buyer_phone")
        st.text_input("Email", key="invoice_center_buyer_emailaddress")


def _render_invoice_settings() -> None:
    with st.container(border=True):
        st.markdown('<div class="section-title">3 發票設定</div>', unsafe_allow_html=True)
        top_cols = st.columns([1, 1, 1])
        with top_cols[0]:
            st.radio("計價方式", ["單價含稅", "單價未稅"], horizontal=True, key="invoice_center_tax_mode")
        with top_cols[1]:
            st.number_input("稅率", min_value=0.0, max_value=1.0, step=0.01, format="%.2f", key="invoice_center_tax_rate")
        with top_cols[2]:
            st.text_input("備註", key="invoice_center_mainremark", placeholder="可輸入內部備註")

        delivery_options = ["會員載具", "手機載具", "自然人憑證", "紙本", "捐贈"]
        if st.session_state.get("invoice_center_buyer_type") == "公司":
            delivery_options = ["紙本", "會員載具", "手機載具", "自然人憑證"]
        st.radio("二聯交付方式 / 載具", delivery_options, horizontal=True, key="invoice_center_delivery_method")

        carrier_cols = st.columns(4)
        with carrier_cols[0]:
            st.text_input("會員載具", key="invoice_center_member_carrier", placeholder="會員 email / 會員編號")
        with carrier_cols[1]:
            st.text_input("手機條碼", key="invoice_center_mobile_barcode", placeholder="/AB1234567")
        with carrier_cols[2]:
            st.text_input("自然人憑證", key="invoice_center_citizen_cert")
        with carrier_cols[3]:
            st.text_input("愛心碼 / 捐贈碼", key="invoice_center_donate_code")
        st.caption("公司客預設三聯；自然人預設二聯。二聯可選會員載具、手機載具、自然人憑證、紙本或捐贈。")


def _render_product_picker() -> None:
    _ensure_product_catalog()
    products = [p for p in st.session_state["invoice_center_product_catalog"] if p.get("狀態") == "啟用"]
    st.markdown("##### 商品查詢")
    search_col, product_col, qty_col, add_col = st.columns([1.4, 2.6, 0.8, 1])
    with search_col:
        keyword = st.text_input("搜尋", placeholder="VIP、清潔、車馬費", key="invoice_product_search")
    if keyword:
        key = keyword.lower()
        products = [p for p in products if key in str(p.get("品號", "")).lower() or key in str(p.get("品名", "")).lower()]
    if not products:
        st.info("查無符合商品，可在商品管理新增。")
        return
    labels = [f"{p['品號']}｜{p['品名']}｜{p['單位']}｜{p['單價']:,}" for p in products]
    with product_col:
        selected_label = st.selectbox("商品", labels, key="invoice_selected_product_label")
    product = products[labels.index(selected_label)]
    with qty_col:
        qty = st.number_input("數量", min_value=1, step=1, value=1, key="invoice_product_qty")
    with add_col:
        st.write("")
        st.write("")
        if st.button("➕ 加入", use_container_width=True):
            rows = _normalize_line_items(st.session_state.get("invoice_center_line_items", []))
            if len(rows) == 1 and not rows[0].get("商品代碼") and not rows[0].get("商品名稱"):
                rows = []
            rows.append(
                {
                    "商品代碼": product.get("品號", ""),
                    "商品名稱": product.get("品名", ""),
                    "單位": product.get("單位", "1"),
                    "數量": qty,
                    "單價": _as_int(product.get("單價"), 0),
                    "備註": "",
                }
            )
            st.session_state["invoice_center_line_items"] = _normalize_line_items(rows)
            st.rerun()


def _render_line_items_editor() -> list[dict[str, Any]]:
    _ensure_line_items()
    edited = st.data_editor(
        st.session_state["invoice_center_line_items"],
        key="invoice_center_line_items_editor",
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "序號": st.column_config.NumberColumn("序號", disabled=True),
            "商品代碼": st.column_config.TextColumn("商品代碼"),
            "商品名稱": st.column_config.TextColumn("商品名稱", required=True),
            "單位": st.column_config.TextColumn("單位", required=True),
            "數量": st.column_config.NumberColumn("數量", min_value=1, step=1, required=True),
            "單價": st.column_config.NumberColumn("單價", min_value=0, step=1, required=True),
            "金額": st.column_config.NumberColumn("金額", disabled=True),
            "備註": st.column_config.TextColumn("備註"),
        },
    )
    rows = _normalize_line_items(edited)
    st.session_state["invoice_center_line_items"] = rows
    return rows


def _render_product_manager() -> None:
    _ensure_product_catalog()
    with st.expander("商品管理：新增 / 修改 / 刪除", expanded=False):
        st.caption("先做畫面與操作；目前商品表存在本次 session，後續可接 Google Sheet 商品主檔。")
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
        col_apply, col_reset, _ = st.columns([1, 1, 3])
        if col_apply.button("💾 套用商品表", use_container_width=True):
            st.session_state["invoice_center_product_catalog"] = [
                dict(p) for p in edited_products if p and p.get("品號") and p.get("狀態") != "刪除"
            ]
            st.rerun()
        if col_reset.button("↩️ 重設預設", use_container_width=True):
            st.session_state["invoice_center_product_catalog"] = [dict(item) for item in DEFAULT_PRODUCTS]
            st.rerun()


def _render_items_section() -> list[dict[str, Any]]:
    with st.container(border=True):
        st.markdown('<div class="section-title">4 商品明細</div>', unsafe_allow_html=True)
        btn_cols = st.columns([1, 1, 1, 4])
        btn_cols[0].button("＋ 新增商品", use_container_width=True)
        btn_cols[1].button("🔍 商品查詢", use_container_width=True)
        btn_cols[2].button("📋 複製上一列", use_container_width=True)
        _render_product_picker()
        rows = _render_line_items_editor()
        _render_product_manager()
        st.caption(f"共 {len([r for r in rows if r.get('商品名稱')])} 項")
        return rows


def _calculate_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    subtotal = sum(_as_int(row.get("金額"), 0) for row in rows)
    tax_rate = float(st.session_state.get("invoice_center_tax_rate", 0.05) or 0)
    if st.session_state.get("invoice_center_tax_status") in ["免稅", "零稅率"]:
        return {"net": subtotal, "tax": 0, "total": subtotal}
    if st.session_state.get("invoice_center_tax_mode") == "單價含稅":
        net = round(subtotal / (1 + tax_rate)) if tax_rate else subtotal
        return {"net": net, "tax": subtotal - net, "total": subtotal}
    tax = round(subtotal * tax_rate)
    return {"net": subtotal, "tax": tax, "total": subtotal + tax}


def _build_payload(area: str, order_no: str, suffix: str, orderdate: date, payway: str, rows: list[dict[str, Any]], totals: dict[str, int]) -> Any:
    invoice_items = [
        InvoiceLineItem(
            goodcode=str(row.get("商品代碼", "")),
            goodname=str(row.get("商品名稱", "")),
            unit=str(row.get("單位", "1")),
            quantity=str(row.get("數量", 1)),
            unitprice=str(row.get("單價", 0)),
            amount=str(row.get("金額", _line_amount(row))),
            fremark=str(row.get("備註", "")),
        )
        for row in rows
        if row.get("商品名稱")
    ]
    return build_invoice_payload(
        area=area,
        order_no=order_no,
        suffix=suffix,
        orderdate=orderdate.isoformat(),
        saleamount=str(totals["total"]),
        buyer_identifier=st.session_state.get("invoice_center_buyer_identifier", ""),
        buyer_name=st.session_state.get("invoice_center_buyer_name", ""),
        buyer_address=st.session_state.get("invoice_center_buyer_address", ""),
        buyer_emailaddress=st.session_state.get("invoice_center_buyer_emailaddress", ""),
        buyer_phone=st.session_state.get("invoice_center_buyer_phone", ""),
        payway=payway,
        mainremark=st.session_state.get("invoice_center_mainremark", ""),
        items=invoice_items,
    )


def _render_summary_and_actions(area: str, order_no: str, suffix: str, invoice_type: str, rows: list[dict[str, Any]]) -> None:
    order = st.session_state.get("invoice_center_backend_order") or {}
    totals = _calculate_totals(rows)
    with st.container(border=True):
        st.markdown('<div class="section-title">5 發票摘要</div>', unsafe_allow_html=True)
        st.metric("未稅金額", f"{totals['net']:,}")
        st.metric("稅額", f"{totals['tax']:,}")
        st.metric("總金額", f"{totals['total']:,}")
        st.info(f"計價方式：{st.session_state.get('invoice_center_tax_mode')}；本發票為 {st.session_state.get('invoice_center_invoice_kind')}。")

    with st.container(border=True):
        st.markdown('<div class="section-title">6 發票狀態</div>', unsafe_allow_html=True)
        st.write("發票狀態：", "已開立" if order.get("invoice_no") else "未開立")
        st.write("發票號碼：", order.get("invoice_no", "-") or "-")
        st.write("開立日期：", "-")
        st.write("開立人員：", "-")

    with st.container(border=True):
        st.markdown('<div class="section-title">7 操作</div>', unsafe_allow_html=True)
        payload = _build_payload(area, order_no, suffix, st.session_state["invoice_center_orderdate"], st.session_state["invoice_center_payway"], rows, totals)
        if st.button("🧾 開立發票（透過 Lemon API）", type="primary", use_container_width=True):
            try:
                purchase_id = str(order.get("purchase_id") or "").strip()
                if not purchase_id:
                    st.warning("尚未取得 purchase_id，請先查詢 Lemon 訂單。")
                elif order.get("invoice_no"):
                    st.success(f"此訂單已開立發票：{order.get('invoice_no')}")
                else:
                    make_invoice(purchase_id, invoice_type=invoice_type)
                    st.info("已送出開立請求，正在重新查詢發票號碼。")
                    refreshed = _load_backend_order(area, order_no, suffix)
                    if refreshed.get("invoice_no"):
                        st.success(f"開立成功：{refreshed.get('invoice_no')}")
                    else:
                        st.warning("已送出，但尚未查到發票號碼，請稍後重新查詢。")
            except Exception as exc:
                st.error(str(exc))
        if st.button("💾 儲存草稿", use_container_width=True):
            st.success("草稿已暫存在目前頁面 session。")
        if st.button("🔍 預覽 Payload", use_container_width=True):
            result = create_invoice_from_payload(payload, dry_run=True)
            st.session_state["invoice_center_preview"] = result.payload
        if st.button("🔄 重新查詢訂單", use_container_width=True):
            try:
                _load_backend_order(area, order_no, suffix)
                st.rerun()
            except Exception as exc:
                st.error(f"重新查詢失敗：{exc}")
        if st.button("🧹 清除所有資料", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key.startswith("invoice_center_"):
                    del st.session_state[key]
            st.rerun()


def _render_invoice_create_tab() -> None:
    _render_invoice_css()
    _bootstrap_invoice_state()
    st.markdown(
        """
        <div class="invoice-hero">
          <h2>🧾 發票中心</h2>
          <p><span class="invoice-chip">公司＝三聯式</span><span class="invoice-chip">自然人＝二聯式</span><span class="invoice-chip">預設單價含稅</span><span class="invoice-chip">Lemon API 開立</span></p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    area, order_no, suffix, invoice_type = _render_top_query()
    left, right = st.columns([3.2, 1.1])
    with left:
        _render_order_overview()
        _render_buyer_section()
        _render_invoice_settings()
        rows = _render_items_section()
    with right:
        _render_summary_and_actions(area, order_no, suffix, invoice_type, rows)

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
