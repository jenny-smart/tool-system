from __future__ import annotations

from datetime import date

import streamlit as st

from .bridge import create_invoice_from_payload
from .config import get_area_options, get_area_status
from .invoice import build_invoice_payload
from .models import InvoiceLineItem
from .query import query_invoice_by_order_id


def _area_select(label: str = "地區") -> str:
    options = get_area_options()
    labels = [item[1] for item in options]
    selected = st.selectbox(label, labels, index=0)
    return dict((display, key) for key, display in options)[selected]


def _render_invoice_create_tab() -> None:
    st.subheader("發票開立")

    col_area, col_order, col_suffix = st.columns([1, 2, 1])
    with col_area:
        area = _area_select()
    with col_order:
        order_no = st.text_input("Lemon 訂單號", placeholder="LC00212058")
    with col_suffix:
        suffix = st.text_input("EI orderid suffix", value="-1")

    col_date, col_amount, col_payway = st.columns([1, 1, 1])
    with col_date:
        orderdate = st.date_input("訂單日期", value=date.today())
    with col_amount:
        saleamount = st.number_input("銷售額", min_value=0, step=1, value=0)
    with col_payway:
        payway = st.text_input("付款方式", value="")

    st.divider()
    st.subheader("買受人")
    buyer_cols = st.columns(2)
    with buyer_cols[0]:
        buyer_identifier = st.text_input("統一編號", value="")
        buyer_name = st.text_input("買受人名稱", value="")
        buyer_phone = st.text_input("電話", value="")
    with buyer_cols[1]:
        buyer_emailaddress = st.text_input("Email", value="")
        buyer_address = st.text_input("地址", value="")
        mainremark = st.text_input("備註", value="")

    st.divider()
    st.subheader("明細")
    item_cols = st.columns([1, 2, 1, 1, 1])
    with item_cols[0]:
        goodcode = st.text_input("品號", value="CLEAN")
    with item_cols[1]:
        goodname = st.text_input("品名", value="清潔服務")
    with item_cols[2]:
        unit = st.text_input("單位", value="式")
    with item_cols[3]:
        quantity = st.number_input("數量", min_value=1, step=1, value=1)
    with item_cols[4]:
        unitprice = st.number_input("單價", min_value=0, step=1, value=saleamount)
    fremark = st.text_input("明細備註", value="")

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
        if st.button("Preview payload", type="primary", use_container_width=True):
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
