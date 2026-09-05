# -*- coding: utf-8 -*-
"""中斷復原時，依訂單編號精確抓後台資料再回填 Sheet。"""
from __future__ import annotations

import orders

_ORIGINAL_FETCH = orders.fetch_order_meta_by_order_no
_INSTALLED = False


def _exact_fetch_order_meta(session, order_no):
    order_no = str(order_no or "").strip()
    if not order_no:
        return _ORIGINAL_FETCH(session, order_no)

    try:
        params = dict(orders.PURCHASE_FILTER_PARAMS_TEMPLATE)
        params["orderNo"] = order_no
        resp = session.get(
            orders.PURCHASE_URL,
            params=params,
            headers=orders.HEADERS,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            for block in orders.extract_order_cards_from_purchase_html(resp.text):
                if str(block.get("order_no") or "").strip() != order_no:
                    continue
                lines = block.get("lines", [])
                service_date, service_time = orders._extract_service_date_time(lines)
                return {
                    "服務人員": orders._extract_staff_line(lines) or "無人力",
                    "服務狀態": orders._extract_status_line(lines) or "未處理",
                    "車馬費": orders._extract_fare_line(lines) or "0",
                    "服務日期": service_date or "",
                    "服務時間": service_time or "",
                }
    except Exception:
        pass

    # 精確查詢失敗才退回既有行為；不因補強功能影響原流程。
    return _ORIGINAL_FETCH(session, order_no)


def install_patch():
    global _INSTALLED
    if _INSTALLED:
        return
    orders.fetch_order_meta_by_order_no = _exact_fetch_order_meta
    _INSTALLED = True
