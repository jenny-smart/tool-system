# -*- coding: utf-8 -*-
"""颱風假訂單移轉：把颱風假當天的訂單，依時段逐筆用後台「修改日期」移到移轉區間。

流程（由上而下逐筆，各筆移到自己原本的時段）：
1. 後台訂單以「服務日期起迄＝颱風假＋已付款」搜尋，依列表由上而下排序。
2. 每筆訂單從移轉區間第一天開始試：讀後台「修改日期」頁內嵌的可選清單
   （就是日期／時間下拉選單的內容），日期選得到、時間也選得到「同一時段」，
   就送出修改日期（日期＋原時段，時段一定相同）；否則跳下一天。
3. 送出後回查訂單，服務日期與時段確實變成目標日期／原時段才算成功；
   否則視為該日無法安排，換下一天。整個區間都排不進去就列為失敗，訂單不動。
"""

import json
import re
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

import orders
import quick_order as qo


SKIPPED_ORDER_STATUSES = {"取消訂單", "已退款"}
CLEAN_TYPE_ID_MAP = {"居家清潔": "1", "辦公室清潔": "2", "裝修細清": "3"}
DAY_TYPES = ["與颱風假相同（平日／週末）", "只排平日", "只排週末", "不限"]


def _compact_period(value):
    match = re.search(r"(\d{1,2}):(\d{2})\s*[-~～]\s*(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return ""
    sh, sm, eh, em = match.groups()
    return f"{int(sh):02d}:{sm}-{int(eh):02d}:{em}"


def _period_sort_key(period):
    bounds = qo._period_bounds_minutes(period)
    return bounds if bounds else (9999, 9999)


def _parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip().replace("/", "-"), "%Y-%m-%d").date()


def parse_date_range(text):
    """解析「2027/03/01-2027/03/05」或「2027-03-01~2027-03-05」。"""
    found = re.findall(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", str(text or ""))
    if len(found) != 2:
        raise ValueError("移轉日期區間格式請輸入：2027/03/01-2027/03/05")
    start, end = (_parse_date(re.sub(r"/", "-", item)) for item in found)
    if start > end:
        raise ValueError("移轉日期區間起日不可晚於迄日")
    return start, end


def _is_workday(day):
    """價格只分平日（週一～五）與週末（週六、日）；國定假日、補假照平日算。"""
    return day.weekday() < 5


def target_dates(start, end, day_type="不限", typhoon_date=None):
    """移轉區間內依日別篩選後的候選日期（由早到晚）。"""
    start, end = _parse_date(start), _parse_date(end)
    if day_type == DAY_TYPES[0]:
        day_type = "只排平日" if typhoon_date and _is_workday(_parse_date(typhoon_date)) else "只排週末"
    days = []
    current = start
    while current <= end:
        workday = _is_workday(current)
        if day_type == "不限" or (day_type == "只排平日" and workday) or (day_type == "只排週末" and not workday):
            days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def _login(env_name, backend_email, backend_password):
    base_url = qo._configure_environment(env_name)
    session = requests.Session()
    if not orders.login(session, backend_email, backend_password):
        raise RuntimeError("後台登入失敗，請確認帳號密碼")
    return session, base_url


def _order_row_from_block(block, typhoon_date):
    lines = block.get("lines", [])
    joined = "\n".join(lines)
    service_date, period_raw = qo._parse_service_date_time_loose(joined)
    if service_date != typhoon_date:
        return None
    period = _compact_period(period_raw)
    if not period:
        return None
    status_match = re.search(r"付款狀態[：:]\s*([^\n]+)", joined)
    status = status_match.group(1).strip() if status_match else ""
    if status in SKIPPED_ORDER_STATUSES:
        return None
    name, phone = "", ""
    for idx, line in enumerate(lines):
        if re.fullmatch(r"09\d{8}", str(line).strip()):
            phone = str(line).strip()
            name = str(lines[idx - 1]).strip() if idx else ""
            break
    staff = orders._extract_staff_line(lines) or ""
    person = len(_staff_names(staff)) or int(qo._extract_person_hour_line(joined)[0] or 2)
    return {
        "order_no": block.get("order_no", ""),
        "period": period,
        "name": name,
        "phone": phone,
        "staff": staff,
        "payment_status": status,
        "address": qo._extract_address_line(lines),
        "person": person,
        "payway": qo._extract_payway_line(joined) or "儲值金",
        "clean_type_id": CLEAN_TYPE_ID_MAP.get(qo._extract_clean_type_line(joined), "1"),
    }


def find_typhoon_orders(env_name, backend_email, backend_password, typhoon_date, max_pages=40):
    """後台訂單以服務日期起迄＝颱風假、已付款搜尋，依後台列表由上而下的順序回傳。"""
    typhoon_date = _parse_date(typhoon_date).strftime("%Y-%m-%d")
    session, base_url = _login(env_name, backend_email, backend_password)
    found = {}
    hit_page_limit = True
    for page in range(1, max_pages + 1):
        params = dict(qo.PURCHASE_FILTER_PARAMS_TEMPLATE)
        params.update({
            "clean_date_s": typhoon_date, "clean_date_e": typhoon_date,
            "purchase_status": qo.PURCHASE_STATUS_PAID, "page": str(page),
        })
        resp = session.get(orders.PURCHASE_URL, params=params, headers=orders.HEADERS, allow_redirects=True)
        if resp.status_code != 200:
            raise RuntimeError(f"訂單查詢失敗（HTTP {resp.status_code}）")
        blocks = orders.extract_order_cards_from_purchase_html(resp.text)
        for block in blocks:
            row = _order_row_from_block(block, typhoon_date)
            if row and row["order_no"] and row["payment_status"] == "已付款":
                found.setdefault(row["order_no"], row)
        if len(blocks) < 20:
            hit_page_limit = False
            break
    rows = list(found.values())
    return rows, {"base_url": base_url, "hit_page_limit": hit_page_limit}


def parse_change_date_page(html_text):
    """解析「修改日期」頁內嵌的可選清單（頁面 JS 用它填日期／時間下拉選單）。

    回傳 {日期: {時段: [專員...]}}；找不到清單時拋錯，不猜測。
    """
    match = re.search(r"var\s+json\s*=\s*'(.*?)';", html_text or "", re.S)
    if not match:
        raise RuntimeError("修改日期頁找不到可選日期清單，未修改")
    try:
        items = json.loads(match.group(1))
    except Exception as exc:
        raise RuntimeError(f"修改日期頁的可選日期清單無法解析，未修改：{exc}")
    options = {}
    for item in items if isinstance(items, list) else []:
        day = str(item.get("date") or "").strip()
        period = _compact_period(item.get("section"))
        if day and period:
            cleaners = item.get("cleaner") or []
            options.setdefault(day, {})[period] = list(cleaners) if isinstance(cleaners, list) else []
    return options


def _change_date_options(session, base_url, order_no, phone=""):
    edit_id = qo._fetch_order_edit_id(session, order_no)
    if not edit_id:
        raise RuntimeError(f"找不到訂單 {order_no} 的後台 ID")
    params = {"phone": phone} if phone else {}
    resp = session.get(
        f"{base_url}/purchase/change_date/{edit_id}", params=params,
        headers=orders.HEADERS, allow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"無法開啟修改日期頁面（HTTP {resp.status_code}）")
    return parse_change_date_page(resp.text)


def _order_now_at(session, order_no, date_s, period):
    block = qo._fetch_purchase_block_for_order_no(session, order_no)
    service_date, period_raw = qo._parse_service_date_time_loose("\n".join(block.get("lines", [])))
    return service_date == date_s and _compact_period(period_raw) == period, service_date, _compact_period(period_raw)


def _staff_names(staff):
    if not staff or staff == "無人力":
        return []
    return [name for name in re.split(r"\s*[Xx×]\s*", staff) if name.strip()]


def transfer_one_order(session, base_url, env_name, row, dates):
    """把一筆訂單移到 dates 中第一個排得進去的日期，時段維持原時段。"""
    order_no, period = row["order_no"], row["period"]
    attempts = []
    try:
        options = _change_date_options(session, base_url, order_no, row.get("phone", ""))
    except Exception as exc:
        return {"success": False, "new_date": "", "message": str(exc), "attempts": attempts}
    for day in dates:
        if day not in options:
            attempts.append(f"{day} 不在修改日期選單")
            continue
        if period not in options[day]:
            attempts.append(f"{day} 無 {period} 時段（可選：{'、'.join(sorted(options[day]))}）")
            continue
        ok, msg = qo._update_order_service_date(session, base_url, order_no, day, period, phone=row.get("phone", ""))
        try:
            block = qo._fetch_purchase_block_for_order_no(session, order_no)
            lines = block.get("lines", [])
            now_date, now_period = qo._parse_service_date_time_loose("\n".join(lines))
            now_period = _compact_period(now_period)
        except Exception as exc:
            return {
                "success": False, "new_date": "",
                "message": f"已送出 {day} 但回查訂單失敗，請到後台確認：{exc}",
                "attempts": attempts,
            }
        if now_date == day and now_period == period:
            staff = orders._extract_staff_line(lines) or ""
            message = f"已移到 {day} {period}"
            if len(_staff_names(staff)) < int(row.get("person") or 0):
                message += f"（專員：{staff or '無'}，人數不足，請到後台確認）"
            return {"success": True, "new_date": day, "new_staff": staff, "message": message, "attempts": attempts}
        if now_date != row["service_date_before"] or now_period != period:
            return {
                "success": False, "new_date": now_date,
                "message": f"送出後訂單變成 {now_date} {now_period}，與預期不符，已停止此筆，請到後台確認",
                "attempts": attempts,
            }
        attempts.append(f"{day} 送出後未改成功（{msg}）")
    return {"success": False, "new_date": "", "message": "移轉區間內都無法安排，訂單未修改", "attempts": attempts}


def transfer_typhoon_orders(env_name, backend_email, backend_password, typhoon_date, rows, dates, progress=None):
    """依 rows 的順序逐筆移轉；rows 每筆需有 order_no／period／phone。"""
    typhoon_date = _parse_date(typhoon_date).strftime("%Y-%m-%d")
    session, base_url = _login(env_name, backend_email, backend_password)
    results = []
    for index, row in enumerate(rows, 1):
        row = {**row, "service_date_before": typhoon_date}
        if progress:
            progress(index, len(rows), row)
        try:
            result = transfer_one_order(session, base_url, env_name, row, dates)
        except Exception as exc:
            result = {"success": False, "new_date": "", "message": str(exc), "attempts": []}
        results.append({**row, **result})
    return results


RESULT_COPY_HEADERS = ["訂單編號", "姓名", "電話", "原日期", "原時段", "新日期", "新時段", "結果", "說明"]


def result_copy_rows(results):
    """移轉結果整理成原日期／時段與移轉後日期／時段。"""
    return [
        {
            "訂單編號": r.get("order_no", ""),
            "姓名": r.get("name", ""),
            "電話": r.get("phone", ""),
            "原日期": r.get("service_date_before", ""),
            "原時段": r.get("period", ""),
            "新日期": r.get("new_date", "") if r.get("success") else "",
            "新時段": r.get("period", "") if r.get("success") else "",
            "結果": "成功" if r.get("success") else "失敗",
            "說明": r.get("message", ""),
        }
        for r in results
    ]


def results_tsv(results):
    """Tab 分隔文字，貼到 Google Sheets 會自動分欄。"""
    lines = ["\t".join(RESULT_COPY_HEADERS)]
    for row in result_copy_rows(results):
        lines.append("\t".join(
            str(row.get(h, "") or "").replace("\t", " ").replace("\n", " ") for h in RESULT_COPY_HEADERS
        ))
    return "\n".join(lines)
