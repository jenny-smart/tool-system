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
    {"品號": "MOVE-FEE", "品名": "異動費", "單位": "1次", "單價": 360, "稅率": "5%", "狀態": "啟用"},
    {"品號": "VIP-MOVE", "品名": "異動費用-VIP", "單位": "1次", "單價": 600, "稅率": "5%", "狀態": "啟用"},
    {"品號": "TRAVEL", "品名": "車馬費(1人)", "單位": "人", "單價": 100, "稅率": "5%", "狀態": "啟用"},
    {"品號": "TOOL-SET", "品名": "工具組", "單位": "1組", "單價": 700, "稅率": "5%", "狀態": "啟用"},
]


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


def _area_select(label: str = "地區") -> str:
    options = get_area_options()
    labels = [item[1] for item in options]
    selected = st.selectbox(label, labels, index=0)
    return dict((display, key) for key, display in options)[selected]


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
    return max(_as_int(row.get("數量"), 1), 1) * max(_as_int(row.get("單價"), 0), 0)


def _normalize_line_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows or []:
        item = {
            "商品代碼": str(row.get("商品代碼") or row.get("品號") or "").strip(),
            "商品名稱": str(row.get("商品名稱") or row.get("品名") or "").strip(),
            "單位": str(row.get("單位") or "1").strip(),
            "數量": max(_as_int(row.get("數量"), 1), 1),
            "單價": max(_as_int(row.get("單價"), 0), 0),
            "備註": str(row.get("備註") or "").strip(),
        }
        item["金額"] = _line_amount(item)
        if any([item["商品代碼"], item["商品名稱"], item["單價"], item["備註"]]):
            result.append(item)
    return result or [{"商品代碼": "", "商品名稱": "", "單位": "1", "數量": 1, "單價": 0, "金額": 0, "備註": ""}]


def _set_default(key: str, value: Any) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _bootstrap_invoice_state() -> None:
    defaults = {
        "invoice_center_orderdate": date.today(),
        "invoice_center_payway": "現金",
        "invoice_center_buyer_type": "自然人",
        "invoice_center_buyer_name": "",
        "invoice_center_company_title": "",
        "invoice_center_buyer_identifier": "",
        "invoice_center_buyer_phone": "",
        "invoice_center_buyer_emailaddress": "",
        "invoice_center_buyer_address": "",
        "invoice_center_tax_mode": "單價含稅",
        "invoice_center_tax_status": "應稅",
        "invoice_center_tax_rate": 0.05,
        "invoice_center_delivery_method": "會員載具",
        "invoice_center_member_carrier": "",
        "invoice_center_mobile_barcode": "",
        "invoice_center_citizen_cert": "",
        "invoice_center_donate_code": "",
        "invoice_center_mainremark": "",
        "invoice_center_invoice_note": "",
        "invoice_center_invoice_type": "一般發票",
    }
    for key, value in defaults.items():
        _set_default(key, value)
    _set_default("invoice_center_product_catalog", [dict(item) for item in DEFAULT_PRODUCTS])
    _set_default(
        "invoice_center_line_items",
        [{"商品代碼": "CLEAN-2P-WD", "商品名稱": "清潔服務 平日(2人)", "單位": "1小時", "數量": 4, "單價": 1200, "金額": 4800, "備註": ""}],
    )


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {max-width: 1280px; padding-top: 1.1rem;}
        [data-testid="stHeader"] {background: rgba(255,255,255,0.72);}
        [data-testid="stMetricValue"] {font-size: 1.25rem;}
        .ic-hero {
            border: 1px solid #d8e5ff; border-radius: 24px; padding: 20px 24px;
            background: linear-gradient(135deg, #f7fbff 0%, #ffffff 55%, #eef6ff 100%);
            box-shadow: 0 12px 30px rgba(20, 72, 140, 0.08); margin-bottom: 16px;
        }
        .ic-hero h1 {font-size: 30px; margin: 0 0 6px 0; color: #102a56;}
        .ic-hero p {margin: 0; color: #667085;}
        .ic-chip {display:inline-block; margin: 8px 6px 0 0; padding: 5px 11px; border-radius:999px; background:#edf5ff; color:#1557ad; border:1px solid #c9dcff; font-size:12px; font-weight:700;}
        .ic-section-title {font-weight: 800; font-size: 18px; color:#1f2937; margin-bottom: 4px;}
        .ic-subtle {color:#667085; font-size: 13px;}
        .ic-status-open {color:#b54708; font-weight:800;}
        .ic-status-done {color:#16824b; font-weight:800;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _apply_payload_to_state(payload: Any) -> None:
    st.session_state["invoice_center_orderdate"] = _as_date(payload.orderdate)
    st.session_state["invoice_center_payway"] = payload.payway or st.session_state.get("invoice_center_payway", "現金")
    st.session_state["invoice_center_buyer_identifier"] = payload.buyer_identifier or ""
    st.session_state["invoice_center_buyer_name"] = payload.buyer_name or ""
    st.session_state["invoice_center_buyer_phone"] = payload.buyer_phone or ""
    st.session_state["invoice_center_buyer_emailaddress"] = payload.buyer_emailaddress or ""
    st.session_state["invoice_center_buyer_address"] = payload.buyer_address or ""
    st.session_state["invoice_center_mainremark"] = payload.mainremark or ""
    if payload.items:
        st.session_state["invoice_center_line_items"] = _normalize_line_items(
            [
                {
                    "商品代碼": item.goodcode,
                    "商品名稱": item.goodname,
                    "單位": item.unit,
                    "數量": item.quantity,
                    "單價": item.unitprice,
                    "備註": item.fremark,
                }
                for item in payload.items
            ]
        )


def _apply_order_defaults(order: dict[str, Any]) -> None:
    buyer_identifier = _order_extra_value(order, "buyer_identifier", "invoice_identifier", "tax_id", "company_no") or str(order.get("buyer_identifier") or "")
    company_title = _order_extra_value(order, "buyer_name", "invoice_title", "company_title") or str(order.get("buyer_name") or "")
    carrier_no = _order_extra_value(order, "carrier_no", "carrierid1", "carrierid2", "carrier_info")
    carrier_type = _order_extra_value(order, "carrier_type", "carriertype", "carrier_label")
    donate_code = _order_extra_value(order, "donate_code", "love_code", "npoban")

    if buyer_identifier:
        st.session_state["invoice_center_buyer_type"] = "公司"
        st.session_state["invoice_center_buyer_identifier"] = buyer_identifier
        if company_title:
            st.session_state["invoice_center_company_title"] = company_title
            st.session_state["invoice_center_buyer_name"] = company_title
    else:
        st.session_state["invoice_center_buyer_type"] = "自然人"

    if donate_code:
        st.session_state["invoice_center_delivery_method"] = "捐贈"
        st.session_state["invoice_center_donate_code"] = donate_code
    elif "自然人" in carrier_type:
        st.session_state["invoice_center_delivery_method"] = "自然人憑證"
        st.session_state["invoice_center_citizen_cert"] = carrier_no
    elif "手機" in carrier_type or carrier_no.startswith("/"):
        st.session_state["invoice_center_delivery_method"] = "手機載具"
        st.session_state["invoice_center_mobile_barcode"] = carrier_no
    elif "會員" in carrier_type or carrier_no:
        st.session_state["invoice_center_delivery_method"] = "會員載具"
        st.session_state["invoice_center_member_carrier"] = carrier_no or str(order.get("email") or "")


def _load_backend_order(area: str, order_no: str, suffix: str) -> dict[str, Any]:
    backend_order, payload = fetch_backend_order_invoice_payload(area, order_no, suffix=suffix)
    order = backend_order.to_dict()
    st.session_state["invoice_center_backend_order"] = order
    _apply_payload_to_state(payload)
    _apply_order_defaults(order)
    return order


def _render_top_query() -> tuple[str, str, str, str]:
    with st.container(border=True):
        st.markdown('<div class="ic-section-title">查詢 Lemon 訂單</div>', unsafe_allow_html=True)
        cols = st.columns([1, 2.2, 1.2, 1])
        with cols[0]:
            area = _area_select()
        with cols[1]:
            order_no = st.text_input("Lemon 訂單號", placeholder="輸入 LC 訂單號，例如 LC00212058")
        with cols[2]:
            invoice_type = st.selectbox("API 開立類型", list(INVOICE_TYPE_OPTIONS.keys()), key="invoice_center_invoice_type")
        with cols[3]:
            st.write("")
            st.write("")
            if st.button("🔍 查詢", type="primary", use_container_width=True):
                try:
                    _load_backend_order(area, order_no, "-1")
                    st.success("已查詢並帶入訂單資料")
                except Exception as exc:
                    st.session_state.pop("invoice_center_backend_order", None)
                    st.error(f"查詢失敗：{exc}")
        with st.expander("進階設定", expanded=False):
            suffix = st.text_input("EI orderid suffix", value="-1", help="一般使用不需調整")
    return area, order_no, suffix, invoice_type


def _render_order_card() -> None:
    order = st.session_state.get("invoice_center_backend_order") or {}
    with st.container(border=True):
        st.markdown('<div class="ic-section-title">1. 訂單資訊</div>', unsafe_allow_html=True)
        cols = st.columns(5)
        cols[0].metric("訂單號", order.get("order_no", "-") or "-")
        cols[1].metric("purchase_id", order.get("purchase_id", "-") or "-")
        cols[2].metric("付款狀態", order.get("paid_status", "-") or "-")
        cols[3].metric("發票狀態", "已開立" if order.get("invoice_no") else "未開立")
        cols[4].metric("發票號碼", order.get("invoice_no", "-") or "-")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.date_input("訂單 / 發票日期", key="invoice_center_orderdate")
        with c2:
            st.selectbox("付款方式", ["現金", "信用卡", "ATM", "轉帳", "LINE Pay", "儲值金", "其他"], key="invoice_center_payway")
        with c3:
            st.text_input("備註", key="invoice_center_mainremark")


def _render_buyer_card() -> None:
    with st.container(border=True):
        st.markdown('<div class="ic-section-title">2. 買受人資料</div>', unsafe_allow_html=True)
        buyer_type = st.radio("買受人類型", ["自然人", "公司"], horizontal=True, key="invoice_center_buyer_type")
        invoice_kind = "三聯式" if buyer_type == "公司" else "二聯式"
        st.info(f"目前發票種類：{invoice_kind}（公司對應三聯；自然人對應二聯）")

        if buyer_type == "公司":
            c1, c2 = st.columns([2, 1])
            with c1:
                title = st.text_input("公司抬頭 / 買受人名稱 *", key="invoice_center_company_title")
                if title:
                    st.session_state["invoice_center_buyer_name"] = title
            with c2:
                st.text_input("統一編號 *", key="invoice_center_buyer_identifier")
        else:
            st.text_input("會員姓名 / 買受人名稱 *", key="invoice_center_buyer_name")

        c1, c2 = st.columns([1, 1])
        with c1:
            st.text_input("電話", key="invoice_center_buyer_phone")
        with c2:
            st.text_input("Email", key="invoice_center_buyer_emailaddress")
        st.text_input("地址", key="invoice_center_buyer_address")


def _render_invoice_settings_card() -> None:
    with st.container(border=True):
        st.markdown('<div class="ic-section-title">3. 發票設定</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            st.radio("含稅 / 未稅", ["單價含稅", "單價未稅"], horizontal=True, key="invoice_center_tax_mode")
        with c2:
            st.selectbox("課稅別", ["應稅", "零稅率", "免稅"], key="invoice_center_tax_status")
        with c3:
            st.number_input("稅率", min_value=0.0, max_value=1.0, value=float(st.session_state.get("invoice_center_tax_rate", 0.05)), step=0.01, format="%.2f", key="invoice_center_tax_rate")

        options = ["會員載具", "手機載具", "自然人憑證", "紙本", "捐贈"]
        if st.session_state.get("invoice_center_buyer_type") == "公司":
            options = ["紙本", "會員載具", "手機載具", "自然人憑證"]
        st.radio("交付方式 / 載具", options, horizontal=True, key="invoice_center_delivery_method")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.text_input("會員載具", key="invoice_center_member_carrier")
        with c2:
            st.text_input("手機條碼", key="invoice_center_mobile_barcode", placeholder="/ABC1234")
        with c3:
            st.text_input("自然人憑證", key="invoice_center_citizen_cert")
        with c4:
            st.text_input("愛心碼 / 捐贈碼", key="invoice_center_donate_code")


def _product_rows() -> list[dict[str, Any]]:
    return [p for p in st.session_state.get("invoice_center_product_catalog", []) if p.get("狀態") == "啟用"]


def _render_product_tools() -> None:
    products = _product_rows()
    with st.container(border=True):
        st.markdown('<div class="ic-section-title">4. 商品明細</div>', unsafe_allow_html=True)
        st.markdown('<span class="ic-subtle">常用商品可直接加入，商品管理維持小區塊，不另開頁。</span>', unsafe_allow_html=True)
        quick = st.columns(4)
        for idx, product in enumerate(products[:4]):
            with quick[idx]:
                if st.button(f"⭐ {product['品名']}", use_container_width=True, key=f"quick_product_{idx}"):
                    rows = _normalize_line_items(st.session_state.get("invoice_center_line_items", []))
                    if len(rows) == 1 and not rows[0].get("商品名稱"):
                        rows = []
                    rows.append({"商品代碼": product["品號"], "商品名稱": product["品名"], "單位": product["單位"], "數量": 1, "單價": product["單價"], "備註": ""})
                    st.session_state["invoice_center_line_items"] = _normalize_line_items(rows)
                    st.rerun()

        c1, c2, c3 = st.columns([1.2, 2.6, 0.9])
        with c1:
            keyword = st.text_input("商品查詢", placeholder="清潔 / 車馬費 / VIP")
        filtered = products
        if keyword:
            key = keyword.lower()
            filtered = [p for p in products if key in p["品號"].lower() or key in p["品名"].lower()]
        labels = [f"{p['品號']}｜{p['品名']}｜{p['單價']:,}" for p in filtered] or ["無符合商品"]
        with c2:
            selected = st.selectbox("選擇商品", labels)
        with c3:
            st.write("")
            st.write("")
            if st.button("＋加入", use_container_width=True, disabled=not filtered):
                product = filtered[labels.index(selected)]
                rows = _normalize_line_items(st.session_state.get("invoice_center_line_items", []))
                if len(rows) == 1 and not rows[0].get("商品名稱"):
                    rows = []
                rows.append({"商品代碼": product["品號"], "商品名稱": product["品名"], "單位": product["單位"], "數量": 1, "單價": product["單價"], "備註": ""})
                st.session_state["invoice_center_line_items"] = _normalize_line_items(rows)
                st.rerun()

        edited = st.data_editor(
            st.session_state["invoice_center_line_items"],
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="invoice_center_line_items_editor",
            column_config={
                "商品代碼": st.column_config.TextColumn("商品代碼"),
                "商品名稱": st.column_config.TextColumn("商品名稱", required=True),
                "單位": st.column_config.TextColumn("單位"),
                "數量": st.column_config.NumberColumn("數量", min_value=1, step=1),
                "單價": st.column_config.NumberColumn("單價", min_value=0, step=1),
                "金額": st.column_config.NumberColumn("金額", disabled=True),
                "備註": st.column_config.TextColumn("備註"),
            },
        )
        st.session_state["invoice_center_line_items"] = _normalize_line_items(edited)

        with st.expander("⚙ 商品管理", expanded=False):
            edited_products = st.data_editor(
                st.session_state["invoice_center_product_catalog"],
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="invoice_center_product_catalog_editor",
                column_config={
                    "品號": st.column_config.TextColumn("品號", required=True),
                    "品名": st.column_config.TextColumn("品名", required=True),
                    "單位": st.column_config.TextColumn("單位", required=True),
                    "單價": st.column_config.NumberColumn("單價", min_value=0, step=1),
                    "稅率": st.column_config.SelectboxColumn("稅率", options=["5%", "0%", "免稅"]),
                    "狀態": st.column_config.SelectboxColumn("狀態", options=["啟用", "停用", "刪除"]),
                },
            )
            c_apply, c_reset, _ = st.columns([1, 1, 3])
            if c_apply.button("套用商品表", use_container_width=True):
                st.session_state["invoice_center_product_catalog"] = [dict(p) for p in edited_products if p and p.get("品號") and p.get("狀態") != "刪除"]
                st.rerun()
            if c_reset.button("重設預設", use_container_width=True):
                st.session_state["invoice_center_product_catalog"] = [dict(item) for item in DEFAULT_PRODUCTS]
                st.rerun()


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


def _build_payload(area: str, order_no: str, suffix: str, rows: list[dict[str, Any]], totals: dict[str, int]) -> Any:
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
        orderdate=st.session_state["invoice_center_orderdate"].isoformat(),
        saleamount=str(totals["total"]),
        buyer_identifier=st.session_state.get("invoice_center_buyer_identifier", ""),
        buyer_name=st.session_state.get("invoice_center_buyer_name", ""),
        buyer_address=st.session_state.get("invoice_center_buyer_address", ""),
        buyer_emailaddress=st.session_state.get("invoice_center_buyer_emailaddress", ""),
        buyer_phone=st.session_state.get("invoice_center_buyer_phone", ""),
        payway=st.session_state.get("invoice_center_payway", ""),
        mainremark=st.session_state.get("invoice_center_mainremark", ""),
        items=invoice_items,
    )


def _render_sidebar_summary(area: str, order_no: str, suffix: str, invoice_type: str) -> None:
    order = st.session_state.get("invoice_center_backend_order") or {}
    rows = _normalize_line_items(st.session_state.get("invoice_center_line_items", []))
    totals = _calculate_totals(rows)
    invoice_kind = "三聯式" if st.session_state.get("invoice_center_buyer_type") == "公司" else "二聯式"
    status = "已開立" if order.get("invoice_no") else "未開立"

    with st.container(border=True):
        st.markdown('<div class="ic-section-title">發票摘要</div>', unsafe_allow_html=True)
        st.metric("未稅金額", f"{totals['net']:,}")
        st.metric("稅額", f"{totals['tax']:,}")
        st.metric("含稅總額", f"{totals['total']:,}")
        st.divider()
        st.write("發票種類：", invoice_kind)
        st.write("計價方式：", st.session_state.get("invoice_center_tax_mode"))
        st.write("交付方式：", st.session_state.get("invoice_center_delivery_method"))

    with st.container(border=True):
        st.markdown('<div class="ic-section-title">發票狀態</div>', unsafe_allow_html=True)
        st.markdown(f"<div class=\"{'ic-status-done' if status == '已開立' else 'ic-status-open'}\">{status}</div>", unsafe_allow_html=True)
        st.write("發票號碼：", order.get("invoice_no") or "-")
        st.write("purchase_id：", order.get("purchase_id") or "-")

    with st.container(border=True):
        st.markdown('<div class="ic-section-title">操作</div>', unsafe_allow_html=True)
        payload = _build_payload(area, order_no, suffix, rows, totals)
        if st.button("🧾 開立發票", type="primary", use_container_width=True):
            try:
                purchase_id = str(order.get("purchase_id") or "").strip()
                if not purchase_id:
                    st.warning("請先查詢 Lemon 訂單，取得 purchase_id。")
                elif order.get("invoice_no"):
                    st.success(f"此訂單已開立發票：{order.get('invoice_no')}")
                else:
                    make_invoice(purchase_id, invoice_type=invoice_type)
                    refreshed = _load_backend_order(area, order_no, suffix)
                    if refreshed.get("invoice_no"):
                        st.success(f"開立成功：{refreshed.get('invoice_no')}")
                    else:
                        st.warning("已送出，但尚未查到發票號碼，請稍後重新查詢。")
            except Exception as exc:
                st.error(str(exc))
        if st.button("🔄 重新查詢", use_container_width=True):
            try:
                _load_backend_order(area, order_no, suffix)
                st.rerun()
            except Exception as exc:
                st.error(f"重新查詢失敗：{exc}")
        if st.button("🔍 預覽 Payload", use_container_width=True):
            result = create_invoice_from_payload(payload, dry_run=True)
            st.session_state["invoice_center_preview"] = result.payload
        if st.button("🧹 清除畫面", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key.startswith("invoice_center_"):
                    del st.session_state[key]
            st.rerun()


def _render_invoice_create_tab() -> None:
    _render_css()
    _bootstrap_invoice_state()
    st.markdown(
        """
        <div class="ic-hero">
          <h1>🧾 發票中心 v2</h1>
          <p>輸入 Lemon 訂單號後帶入資料；公司對應三聯式，自然人對應二聯式，預設單價含稅。</p>
          <span class="ic-chip">公司＝三聯式</span>
          <span class="ic-chip">二聯＝會員/手機/紙本</span>
          <span class="ic-chip">預設含稅</span>
          <span class="ic-chip">Lemon API</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    area, order_no, suffix, invoice_type = _render_top_query()
    left, right = st.columns([3.15, 1.05])
    with left:
        _render_order_card()
        _render_buyer_card()
        _render_invoice_settings_card()
        _render_product_tools()
    with right:
        _render_sidebar_summary(area, order_no, suffix, invoice_type)

    preview = st.session_state.get("invoice_center_preview")
    if preview:
        with st.expander("Payload 預覽", expanded=False):
            st.json(preview)


def _render_allowance_tab() -> None:
    st.subheader("折讓單")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.text_input("原發票號碼")
    with c2:
        st.date_input("折讓日期", value=date.today())
    with c3:
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
            rows = query_invoice_by_order_id(order_id, date1.isoformat(), date2.isoformat(), area=area, captcha=captcha or None)
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("查無資料")
        except Exception as exc:
            st.error(f"查詢失敗：{exc}")
    st.info("PDF/XML 下載 API 尚待補齊。")


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
