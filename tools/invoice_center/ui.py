from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from .bridge import create_invoice_from_payload, fetch_backend_order_invoice_payload
from .config import get_area_options, get_area_status
from .invoice import build_invoice_payload
from .models import InvoiceLineItem, to_decimal
from .query import query_invoice_by_order_id


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


def _bootstrap_invoice_state() -> None:
    defaults = {
        "invoice_center_orderdate": date.today(),
        "invoice_center_saleamount": 0,
        "invoice_center_payway": "",
        "invoice_center_buyer_identifier": "",
        "invoice_center_buyer_name": "",
        "invoice_center_buyer_phone": "",
        "invoice_center_buyer_emailaddress": "",
        "invoice_center_buyer_address": "",
        "invoice_center_mainremark": "",
        "invoice_center_goodcode": "CLEAN",
        "invoice_center_goodname": "清潔服務",
        "invoice_center_unit": "式",
        "invoice_center_quantity": 1,
        "invoice_center_unitprice": 0,
        "invoice_center_fremark": "",
    }
    for key, value in defaults.items():
        _set_default(key, value)


def _render_backend_order_summary() -> None:
    order = st.session_state.get("invoice_center_backend_order")
    if not order:
        return
    st.success(f"已帶入 Lemon 訂單：{order.get('order_no', '')}")
    st.dataframe(
        [
            {
                "訂單號": order.get("order_no", ""),
                "姓名": order.get("customer_name", ""),
                "電話": order.get("phone", ""),
                "金額": order.get("amount", ""),
                "付款方式": order.get("payway", ""),
                "付款狀態": order.get("paid_status", ""),
                "服務日期": order.get("service_date", ""),
                "發票號碼": order.get("invoice_no", ""),
            }
        ],
        use_container_width=True,
        hide_index=True,
    )
    if order.get("items"):
        st.caption("服務項目：" + "、".join(order.get("items", [])))


def _render_invoice_create_tab() -> None:
    st.subheader("發票開立")
    _bootstrap_invoice_state()

    col_area, col_order, col_suffix = st.columns([1, 2, 1])
    with col_area:
        area = _area_select()
    with col_order:
        order_no = st.text_input("Lemon 訂單號", placeholder="LC00212058")
    with col_suffix:
        suffix = st.text_input("EI orderid suffix", value="-1")

    if st.button("查詢 Lemon 訂單並帶入", type="primary", use_container_width=True):
        try:
            backend_order, loaded_payload = fetch_backend_order_invoice_payload(
                area,
                order_no,
                suffix=suffix,
            )
            st.session_state["invoice_center_backend_order"] = backend_order.to_dict()
            _apply_payload_to_state(loaded_payload)
        except Exception as exc:
            st.session_state.pop("invoice_center_backend_order", None)
            st.error(f"Lemon 訂單查詢失敗：{exc}")

    _render_backend_order_summary()

    col_date, col_amount, col_payway = st.columns([1, 1, 1])
    with col_date:
        orderdate = st.date_input("訂單日期", key="invoice_center_orderdate")
    with col_amount:
        saleamount = st.number_input(
            "銷售額",
            min_value=0,
            step=1,
            key="invoice_center_saleamount",
        )
    with col_payway:
        payway = st.text_input("付款方式", key="invoice_center_payway")

    st.divider()
    st.subheader("買受人")
    buyer_cols = st.columns(2)
    with buyer_cols[0]:
        buyer_identifier = st.text_input("統一編號", key="invoice_center_buyer_identifier")
        buyer_name = st.text_input("買受人名稱", key="invoice_center_buyer_name")
        buyer_phone = st.text_input("電話", key="invoice_center_buyer_phone")
    with buyer_cols[1]:
        buyer_emailaddress = st.text_input("Email", key="invoice_center_buyer_emailaddress")
        buyer_address = st.text_input("地址", key="invoice_center_buyer_address")
        mainremark = st.text_input("備註", key="invoice_center_mainremark")

    st.divider()
    st.subheader("明細")
    item_cols = st.columns([1, 2, 1, 1, 1])
    with item_cols[0]:
        goodcode = st.text_input("品號", key="invoice_center_goodcode")
    with item_cols[1]:
        goodname = st.text_input("品名", key="invoice_center_goodname")
    with item_cols[2]:
        unit = st.text_input("單位", key="invoice_center_unit")
    with item_cols[3]:
        quantity = st.number_input(
            "數量",
            min_value=1,
            step=1,
            key="invoice_center_quantity",
        )
    with item_cols[4]:
        unitprice = st.number_input(
            "單價",
            min_value=0,
            step=1,
            key="invoice_center_unitprice",
        )
    fremark = st.text_input("明細備註", key="invoice_center_fremark")

    item_amount = unitprice * quantity
    payload = build_invoice_payload(
        area=area,
        order_no=order_no,
        suffix=suffix,
        orderdate=orderdate.isoformat(),
        saleamount=saleamount or item_amount,
        buyer_identifier=buyer_identifier,
        buyer_name=buyer_name,
        buyer_address=buyer_address,
        buyer_emailaddress=buyer_emailaddress,
        buyer_phone=buyer_phone,
        payway=payway,
        mainremark=mainremark,
        items=[
            InvoiceLineItem(
                goodcode=goodcode,
                goodname=goodname,
                unit=unit,
                quantity=str(quantity),
                unitprice=str(unitprice),
                amount=str(item_amount),
                fremark=fremark,
            )
        ],
    )

    preview_col, submit_col = st.columns([1, 1])
    with preview_col:
        if st.button("Preview payload", use_container_width=True):
            result = create_invoice_from_payload(payload, dry_run=True)
            st.session_state["invoice_center_preview"] = result.payload
    with submit_col:
        allow_submit = st.checkbox("允許正式送出", value=False)
        captcha = st.text_input("Captcha", value="", type="password")
        if st.button("送出 EI", use_container_width=True, disabled=not allow_submit):
            try:
                result = create_invoice_from_payload(
                    payload,
                    dry_run=False,
                    captcha=captcha or None,
                )
                if result.success:
                    st.success(result.message)
                else:
                    st.error(result.error or result.message)
                st.session_state["invoice_center_preview"] = result.payload
            except Exception as exc:
                st.error(f"送出失敗：{exc}")

    preview = st.session_state.get("invoice_center_preview")
    if preview:
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
