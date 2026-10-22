"""
tools/annual_rollover/gift_download.py

春節禮品名單統計目前使用 download_gift_settlement（儲值金結算）；download_gift_source（儲值金訂單）保留備用。

春節禮品名單：從檸檬後台下載各地區前一年度的儲值金訂單（訂單格式，跟月排程「上下半月訂單」的儲值金
匯出相同：buy=5 儲值金、已付款），改用付款日期篩整年。新竹／高雄共用後台，跟月排程一樣用客戶歷史地址
分區，只留本區訂單。
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable, Tuple

import requests


def gift_export_params(prev_year: int) -> dict:
    return {
        "keyword": "", "name": "", "phone": "", "orderNo": "",
        "date_s": "", "date_e": "",
        "clean_date_s": "", "clean_date_e": "",
        "paid_at_s": f"{prev_year}-01-01", "paid_at_e": f"{prev_year}-12-31",
        "refundDateS": "", "refundDateE": "",
        "buy": "5", "area_id": "", "isCharge": "", "isRefund": "", "payway": "",
        "purchase_status": "1", "progress_status": "", "invoiceStatus": "",
        "otherFee": "", "orderBy": "", "p_board": "on",
    }


def download_gift_source(city: str, prev_year: int, log: Callable[[str], None] = print) -> Tuple[bytes, str, int]:
    """回傳 (檔案內容, 副檔名, 筆數)。"""
    from tools.scheduled_monthly import half_month_orders as hm

    acc = hm.load_accounts().get(city) or {}
    if not acc.get("email") or not acc.get("password"):
        raise RuntimeError(f"缺少{city}後台帳號密碼")
    session = requests.Session()
    hm.login(session, acc["email"], acc["password"])
    params = gift_export_params(prev_year)
    res = session.get(hm.EXPORT_URL, params=params, headers=hm.HEADERS, allow_redirects=True, timeout=120)
    res.raise_for_status()
    hm.assert_excel_content(res.content, res.headers.get("Content-Type", ""))
    content = res.content

    if city in hm.STORED_VALUE_MERGE_CITIES:
        records = hm.purchase_records(session, paid_at_s=params["paid_at_s"], paid_at_e=params["paid_at_e"],
                                      buy="5", purchase_status="1", p_board="on")
        selected, cache = set(), {}
        for order in records:
            key = (hm._customer_key(order.get("name")), str(order.get("member_id") or ""),
                   str(order.get("phone") or ""))
            if key not in cache:
                cache[key] = hm.stored_value_customer_city(session, order)
            if cache[key] == city:
                selected.add(str(order.get("order_no") or "").strip())
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.xlsx")
            output = os.path.join(temp_dir, "output.xlsx")
            hm.save_preserved_excel(content, source)
            hm.filter_workbook_rows(source, output, selected)
            with open(output, "rb") as f:
                content = f.read()
        log(f"  {city} 儲值金地址核對：候選 {len(records)} 筆 → 本區 {len(selected)} 筆")

    count = len(hm.read_excel(content))
    return content, ("xlsx" if content[:2] == b"PK" else "xls"), count


SETTLEMENT_EXPORT_URL = "https://backend.lemonclean.com.tw/member/export_stored_value"


def download_gift_settlement(city: str, prev_year: int = 0, log: Callable[[str], None] = print
                             ) -> Tuple[bytes, str, int]:
    """儲值金結算（會員匯出：B 姓名、D 電話、E 地址、F LINE@、G 購買日期、H 購買項目、I 購買原價），
    跟月排程「儲值金結算」同一個匯出（沒有日期參數，是下載當下的資料）。回傳 (檔案內容, 副檔名, 筆數)。"""
    from tools.scheduled_monthly import half_month_orders as hm

    acc = hm.load_accounts().get(city) or {}
    if not acc.get("email") or not acc.get("password"):
        raise RuntimeError(f"缺少{city}後台帳號密碼")
    session = requests.Session()
    hm.login(session, acc["email"], acc["password"])
    res = session.get(SETTLEMENT_EXPORT_URL, headers=hm.HEADERS, allow_redirects=True, timeout=120)
    res.raise_for_status()
    hm.assert_excel_content(res.content, res.headers.get("Content-Type", ""))
    content = res.content
    return content, ("xlsx" if content[:2] == b"PK" else "xls"), len(hm.read_excel(content))
