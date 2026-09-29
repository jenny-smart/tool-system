"""Read customer details by exact invoice number; never send LINE messages."""
from __future__ import annotations

from urllib.parse import urlparse

from tools.lemon_backend.client import BackendClient
from tools.lemon_backend.orders import _extract_purchase_list_data, purchase_params


def customer_from_html(html: str, invoice_no: str) -> tuple[str, str, str]:
    matches = [item for item in _extract_purchase_list_data(html)
               if str(item.get("invoice_no") or "").strip().upper() == invoice_no.upper()]
    customers = set()
    for item in matches:
        member = item.get("member") or {}
        name = str(item.get("name") or member.get("name") or "").strip()
        address = str(item.get("address") or "").strip()
        link = str(member.get("line") or "").strip()
        if link and (urlparse(link).scheme not in {"https", "http", "line"}):
            raise RuntimeError(f"發票 {invoice_no} 的 LINE 欄位不是連結，請人工確認")
        customers.add((name, address, link))
    if len(customers) != 1:
        raise RuntimeError(f"發票 {invoice_no} 找到 {len(customers)} 組客戶資料，請人工確認")
    result = customers.pop()
    if not result[0] or not result[1]:
        raise RuntimeError(f"發票 {invoice_no} 缺少訂購人姓名或地址")
    return result


def lookup_customers(area: str, invoices: list[str]) -> dict[str, tuple[str, str, str]]:
    client = BackendClient(area)
    result = {}
    for invoice in dict.fromkeys(invoices):
        response = client.get_purchase_page(purchase_params(keyword=invoice))
        result[invoice] = customer_from_html(response.text, invoice)
        if not result[invoice][2]:
            print(f"[{area}] 發票 {invoice} 後台未提供 LINE 連結，保留姓名純文字")
    return result
