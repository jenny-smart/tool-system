from __future__ import annotations

from datetime import datetime

from .models import BackendOrder
from .orders import (
    extract_address_from_text,
    normalize_address,
    parse_purchase_list_page,
    purchase_params,
)
from .session import LemonBackendSession


def same_address(a: str, b: str) -> bool:
    left = normalize_address(a)
    right = normalize_address(b)
    return bool(left and right and left == right)


def parse_date(value: str):
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except Exception:
            pass
    return None


def search_orders_by_order_no(session: LemonBackendSession, order_no: str) -> list[BackendOrder]:
    response = session.get("/purchase", params=purchase_params(orderNo=order_no))
    response.raise_for_status()
    return parse_purchase_list_page(response.text, session.base_url)


def search_all_orders_by_phone(session: LemonBackendSession, phone: str) -> list[BackendOrder]:
    response = session.get("/purchase", params=purchase_params(phone=phone))
    response.raise_for_status()
    return parse_purchase_list_page(response.text, session.base_url)


def search_by_conditions(
    session: LemonBackendSession,
    date_mode: str,
    date_start: str,
    date_end: str,
    purchase_status_name: str = "全部",
) -> list[BackendOrder]:
    status_map = {"未付款": "0", "已付款": "1"}
    statuses = ["0", "1"] if purchase_status_name == "全部" else [status_map.get(purchase_status_name, "")]
    merged: dict[str, BackendOrder] = {}
    for status in statuses:
        params = purchase_params(purchase_status=status, progress_status="0")
        if date_mode == "服務日期":
            params["clean_date_s"] = (date_start or "").replace("/", "-")
            params["clean_date_e"] = (date_end or "").replace("/", "-")
        else:
            params["date_s"] = (date_start or "").replace("/", "-")
            params["date_e"] = (date_end or "").replace("/", "-")
        response = session.get("/purchase", params=params)
        response.raise_for_status()
        for order in parse_purchase_list_page(response.text, session.base_url):
            merged[order.order_no] = order
    return list(merged.values())


def find_orders_by_address(session: LemonBackendSession, address: str, phone: str = "") -> list[BackendOrder]:
    candidates = search_all_orders_by_phone(session, phone) if phone else search_by_conditions(session, "訂單日期", "", "", "全部")
    return [order for order in candidates if same_address(order.address, address)]


__all__ = [
    "extract_address_from_text",
    "find_orders_by_address",
    "parse_date",
    "same_address",
    "search_all_orders_by_phone",
    "search_by_conditions",
    "search_orders_by_order_no",
]
