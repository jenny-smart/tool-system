# ============================================================
# 檔名：change_order.py
# 版本：v1.6
# 模組：清潔異動模組：車馬費 / 異動服務收款 / 異動服務退款
# 建立日期：2026-06-22
# 最後更新：2026-06-24
#
# Change Log
# v1.4
# v1.5
# - 修正 v1.5 遺留的 row_spec 未定義錯誤。
# - 保留 get_pending_rows(row_spec=...) 相容 memoapp.py 指定列號掃描。
# - 指定列號支援單列、多列與區間，例如 19、19,21、19-22。
#
# v1.4
# - 修正階段 B 回填後台：isCharge / isRefund 依後台 radio value 寫入。
# - 待加收 / 已加收時，M 欄寫入加收日期。
# - 加收備註若有發票號碼，補上「，開立發票{發票號碼}」，並插入財務備註第一列，不覆蓋原內容。
# - 待退款 / 已退款時，AC 欄寫入退款日期。
# - 待退備註若有退款日期，補上「，{退款日期}已退款」，並插入財務備註第一列，不覆蓋原內容。
# - 階段 A 寫入 Sheet 時保留 K 欄公式，不覆蓋台北 / 台中清潔異動工作表 K 欄。
# v1.3
# - 階段 B 支援使用者輸入指定 Sheet 列號後再回填。
# v1.2
# - 新增 prod/dev 環境切換，並同步台中清潔異動工作表 gid。
# ============================================================
# -*- coding: utf-8 -*-
"""
清潔異動模組：車馬費 / 異動服務收款 / 異動服務退款

整體分兩個階段，互相獨立執行：
  階段 A：fetch_and_calc()  → 查訂單 + 試算金額 → 寫入「清潔異動工作表」
  階段 B：sync_pending_rows() → 讀「清潔異動工作表」待處理列 → 回填後台 purchase/edit → 更新 Sheet 狀態
"""

import re
import math
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

try:
    import streamlit as st
except Exception:
    st = None

BASE_URLS = {
    "prod": "https://backend.lemonclean.com.tw",
    "dev": "https://backend-dev.lemonclean.com.tw",
}
CURRENT_ENV = "prod"
BASE_URL = BASE_URLS[CURRENT_ENV]


def set_env(env: str = "prod"):
    """切換後台環境，讓服務異動查詢與回填跟登入區選擇的 prod/dev 一致。"""
    global CURRENT_ENV, BASE_URL
    env = (env or "prod").strip().lower()
    if env not in BASE_URLS:
        raise ValueError(f"不支援的環境：{env}（目前支援：{list(BASE_URLS.keys())}）")
    CURRENT_ENV = env
    BASE_URL = BASE_URLS[env]
    return BASE_URL


def get_base_url() -> str:
    """回傳目前服務異動模組使用的後台網址。"""
    return BASE_URL

# 週六日與台灣國定假日不算工作日。此表先放 2026 年常用放假日；
# 後續若跨年使用，只要補同格式日期即可。
TAIWAN_PUBLIC_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 28),
    date(2026, 4, 4),
    date(2026, 4, 5),
    date(2026, 5, 1),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 10, 10),
    date(2026, 12, 25),
}




def _today_taipei_str(today: date = None) -> str:
    """回傳台北時區登記日期字串，避免 Streamlit 主機使用 UTC 導致日期少一天。"""
    if today:
        return today.strftime("%Y/%m/%d")
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")


def _money_int(value, default: int = 0) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return default


def get_travel_fee(order: dict) -> int:
    """車馬費。優先使用訂單付款資訊解析出的金額；沒有資料時回傳 0。"""
    return _money_int((order or {}).get("travel_fee", 0))


def get_service_amount(order: dict) -> int:
    """服務費基礎 = 總金額 - 車馬費；退款相關比例均以此為基礎。"""
    total = _money_int((order or {}).get("total", 0))
    travel_fee = get_travel_fee(order)
    return max(total - travel_fee, 0)

# ============================================================
# Google Sheet 連線（比照 memo.py 用 service account）
# ============================================================

# 兩個地區各自的清潔異動工作表（Sheet ID 取自您提供的網址）
SHEET_IDS = {
    "台北": "1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44",
    "台中": "1AlsgBL7uAooiU8hb0v-02J2MdBgDVJtGHgvD3U84hCM",
}

# 對應網址列上的 gid，用來精準定位分頁（比用分頁名稱比對更穩，不怕改名）
SHEET_GIDS = {
    "台北": 759897417,
    "台中": 759897417,
}

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gspread_client = None


def _secret_value(key, default=""):
    if st is None:
        return default
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def _get_gspread_client():
    """
    建立（並快取）gspread client。
    服務帳號設定比照 memo.py：優先讀 st.secrets["gcp_service_account"]（TOML 區塊），
    沒有的話再試 st.secrets["GOOGLE_SERVICE_ACCOUNT"]（整包 JSON 字串）。
    若您 memo.py 用的 key 名稱不同，請把下面兩個 _secret_value(...) 的 key 改成一致即可。
    """
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client

    if st is None:
        raise RuntimeError("找不到 streamlit，無法讀取 st.secrets 取得 Google 憑證")

    sa_info = None

    # 依序嘗試這幾個 key（實際命名以 memo.py 為準：GOOGLE_SERVICE_ACCOUNT 是 TOML 區塊）
    for key in ("GOOGLE_SERVICE_ACCOUNT", "gcp_service_account"):
        try:
            block = st.secrets.get(key, None)
        except Exception:
            block = None

        if not block:
            continue

        if isinstance(block, str):
            # 萬一是整包 JSON 字串
            import json
            sa_info = json.loads(block)
        else:
            # TOML 區塊讀出來是 AttrDict / Mapping，直接轉成一般 dict
            sa_info = dict(block)
        break

    if not sa_info:
        raise RuntimeError(
            "找不到 Google 服務帳號憑證，請確認 secrets.toml 裡有 [GOOGLE_SERVICE_ACCOUNT] "
            "區塊或 GOOGLE_SERVICE_ACCOUNT（JSON 字串），命名請跟 memo.py 現有設定一致"
        )

    creds = Credentials.from_service_account_info(sa_info, scopes=_SCOPES)
    _gspread_client = gspread.authorize(creds)
    return _gspread_client


def get_worksheet(region: str, tab_name: str = "清潔異動"):
    """
    依地區回傳對應的 gspread worksheet 物件。
    優先用 gid（SHEET_GIDS）精準定位分頁；若該地區沒有設定 gid，
    退而用 tab_name 嘗試找同名分頁，最後 fallback 用該試算表第一個分頁。
    """
    if region not in SHEET_IDS:
        raise ValueError(f"不支援的地區：{region}（目前支援：{list(SHEET_IDS.keys())}）")

    client = _get_gspread_client()
    sh = client.open_by_key(SHEET_IDS[region])

    gid = SHEET_GIDS.get(region)
    if gid is not None:
        for ws in sh.worksheets():
            if ws.id == gid:
                return ws

    try:
        return sh.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        return sh.get_worksheet(0)


# ============================================================
# 共用：欄位常數（對照清潔異動工作表）
# ============================================================

COL = {
    "A_類別": "A",
    "B_狀態": "B",
    "C_細項": "C",
    "E_登記日期": "E",
    "F_客戶類別": "F",
    "G_訂單編號": "G",
    "H_客人姓名": "H",
    "I_原服務時間": "I",
    "J_備註": "J",
    "K_後台備註": "K",
    "M_收款時間": "M",
    "N_收款金額": "N",
    "O_收款發票號碼": "O",
    "P_退款銀行名稱": "P",
    "Q_銀行帳號": "Q",
    "R_付款方式": "R",
    "S_退款金額": "S",
    "T_匯款金額": "T",
    "X_退款訂單發票號碼": "X",
    "Y_二聯三聯": "Y",
    "AA_發票折讓處理時間": "AA",
    "AB_折讓單號碼": "AB",
    "AC_退款時間": "AC",
}

STATUS_PENDING_CHARGE = "待收款"
STATUS_PENDING_REFUND = "待退款"
STATUS_DONE_CHARGE = "已收款"
STATUS_DONE_REFUND = "已退款"
SYNC_STATUSES = {
    STATUS_PENDING_CHARGE,
    STATUS_PENDING_REFUND,
    STATUS_DONE_CHARGE,
    STATUS_DONE_REFUND,
}

TYPE_FARE = "車馬費發票"
TYPE_CHARGE = "異動服務收款"
TYPE_REFUND = "異動服務退款"
TYPE_COMPLAINT_REFUND = "客訴退款"
TYPE_DAMAGE_REFUND = "物損退款"


# ============================================================
# 工具函式
# ============================================================

def _parse_period_hours(period_text: str) -> float:
    """ '14:00 - 18:00' -> 4.0；長時段扣 1 小時休息，例如 09:00-16:00 -> 6.0。 """
    m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", period_text or "")
    if not m:
        return 0.0
    h1, m1, h2, m2 = map(int, m.groups())
    hours = ((h2 * 60 + m2) - (h1 * 60 + m1)) / 60
    if hours >= 6:
        hours -= 1
    return round(max(hours, 0), 2)


def _is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in TAIWAN_PUBLIC_HOLIDAYS


def _is_weekend_or_holiday(d: date) -> bool:
    return not _is_workday(d)


def _count_workdays_before(service_date: date, today: date = None) -> int:
    """
    計算今天到服務日前一日之間還剩幾個工作天（不含服務日）。
    週六日與例假日不算工作日；若今天不是工作日，從下一個工作日開始算。
    例：2026-06-21（日）異動 2026-06-23（二），只算 2026-06-22（一）= 1 天。
    當天/已過去 -> 0
    """
    today = today or date.today()
    if service_date <= today:
        return 0
    days = 0
    d = today
    while d < service_date:
        if _is_workday(d):
            days += 1
        d += timedelta(days=1)
    return days


# ============================================================
# 階段 A-1：查訂單基本資料
# ============================================================

def _extract_service_date(date_cell_text: str):
    """ 從服務日期欄文字中擷取日期，支援 2026-06-24 或 2026/06/24 格式 """
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", date_cell_text or "")
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _parse_order_row(row) -> dict:
    """ 解析 /purchase 查詢結果頁裡的單一筆 <tr>，回傳訂單基本資料 dict（找不到訂單編號則回傳 None） """
    checkbox = row.select_one('input[name="purchase_id[]"]')
    purchase_id = checkbox["value"] if checkbox else None
    order_no_label = row.select_one("td label")
    order_no = order_no_label.get_text(strip=True).split()[-1] if order_no_label else ""
    if not order_no:
        return None

    name_tag = row.select_one('a[href*="/member?keyword"]')
    customer_name = name_tag.get_text(strip=True) if name_tag else ""

    tds = row.select("td")
    date_cell = tds[2] if len(tds) > 2 else None
    date_cell_text = date_cell.get_text("\n", strip=True) if date_cell else ""

    period_match = re.search(r"\d{2}:\d{2}\s*-\s*\d{2}:\d{2}", date_cell_text)
    period_text = period_match.group(0) if period_match else ""

    cleaner_count = len(date_cell.select('a[href*="schedule/edit"]')) if date_cell else 0
    service_date_obj = _extract_service_date(date_cell_text)

    pay_cell = tds[3] if len(tds) > 3 else None
    pay_cell_text = pay_cell.get_text("\n", strip=True) if pay_cell else ""

    total_match = re.search(r"總金額[：:]\s*([\d,]+)", pay_cell_text)
    total = int(total_match.group(1).replace(",", "")) if total_match else 0

    travel_fee_match = re.search(r"車馬費[：:]\s*([\d,]+)", pay_cell_text)
    travel_fee = int(travel_fee_match.group(1).replace(",", "")) if travel_fee_match else 0

    payway = "儲值金" if "儲值金" in pay_cell_text else "非儲值金"

    invoice_match = re.search(r"發票[：:]\s*([A-Z0-9]+)", pay_cell_text)
    invoice_no = invoice_match.group(1) if invoice_match else ""

    carrier_type = "三聯式" if "統編" in pay_cell_text or "三聯" in pay_cell_text else "二聯式"
    is_paid = ("已付款" in pay_cell_text) and ("未付款" not in pay_cell_text)

    return {
        "purchase_id": purchase_id,
        "order_no": order_no,
        "customer_name": customer_name,
        "period_text": period_text,
        "service_hours": _parse_period_hours(period_text),
        "cleaner_count": cleaner_count,
        "total": total,
        "travel_fee": travel_fee,
        "service_amount": max(total - travel_fee, 0),
        "payway": payway,           # 儲值金 / 非儲值金
        "invoice_no": invoice_no,
        "carrier_type": carrier_type,  # 二聯式 / 三聯式
        "raw_date_cell": date_cell_text,
        "service_date": service_date_obj,
        "is_paid": is_paid,
        "pay_status_text": pay_cell_text,
    }


def fetch_order_basic(keyword: str, session: requests.Session, ui_logger=None, by="orderNo"):
    """
    依電話或訂單編號查詢 /purchase，回傳該訂單基本資料 dict（取第一筆符合的列）。
    by: "orderNo" 或 "phone"
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    params = {by: keyword}
    log(f"查詢訂單：{by}={keyword}")

    resp = session.get(f"{BASE_URL}/purchase", params=params, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    row = soup.select_one("table tbody tr")
    if not row:
        log("⚠️ 查無資料")
        return None

    result = _parse_order_row(row)
    if not result:
        log("⚠️ 查無資料")
        return None

    log(f"✅ 查到訂單 {result['order_no']}，總金額 {result['total']}，"
        f"{result['cleaner_count']} 人，{result['period_text']}")
    return result


def _select_upcoming_paid_orders(parsed: list, today: date = None) -> list:
    """從已解析訂單中挑出已付款且尚未服務的訂單，依服務日期由近到遠排序。"""
    today = today or date.today()
    paid_with_date = [
        p for p in parsed
        if p.get("is_paid") and p.get("service_date") and p["service_date"] >= today
    ]
    return sorted(paid_with_date, key=lambda p: (p["service_date"], p.get("order_no", "")))


def fetch_upcoming_paid_orders_by_phone(phone: str, session: requests.Session, ui_logger=None):
    """
    依電話查詢 /purchase，找出目前「已付款且尚未服務」的訂單列表。
    回傳 list of dict（內容同 fetch_order_basic 的結果），查無資料則回傳空 list。
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    log(f"查詢電話：{phone}")

    resp = session.get(f"{BASE_URL}/purchase", params={"phone": phone}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = soup.select("table tbody tr")
    if not rows:
        log("⚠️ 查無資料")
        return []

    parsed = []
    for row in rows:
        info = _parse_order_row(row)
        if info:
            parsed.append(info)

    result = _select_upcoming_paid_orders(parsed)
    if not result:
        log("⚠️ 查無「已付款、尚未服務」且有服務日期的訂單")
        return []

    log(f"✅ 找到 {len(result)} 筆目前已付款未服務訂單，已依服務日期由近到遠排序")
    return result


def fetch_recent_paid_orders_by_phone(phone: str, session: requests.Session, ui_logger=None):
    """相容舊呼叫名稱；實際回傳目前所有已付款未服務訂單。"""
    return fetch_upcoming_paid_orders_by_phone(phone, session=session, ui_logger=ui_logger)


# ============================================================
# 階段 A-2：試算金額
# ============================================================

def _col_letter_to_index(letter: str) -> int:
    """ 'A' -> 0, 'Z' -> 25, 'AA' -> 26, 'AB' -> 27, 'AC' -> 28 ... 支援多字母欄位 """
    result = 0
    for ch in letter.strip().upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _format_service_datetime(service_date: date, period_text: str) -> str:
    """ 組成 I 欄要寫入的文字：服務日期＋星期＋時段，例如 2026-06-24 (三) 09:00 - 12:00 """
    if not service_date:
        return period_text or ""
    weekday_map = ["一", "二", "三", "四", "五", "六", "日"]
    weekday = weekday_map[service_date.weekday()]
    date_part = f"{service_date.strftime('%Y-%m-%d')} ({weekday})"
    return f"{date_part} {period_text}".strip() if period_text else date_part


def _change_timing_label(fee_info: dict) -> str:
    """組成 J 欄使用的異動時間說明。"""
    workdays = fee_info.get("workdays", 0)
    if workdays <= 0:
        return "服務當天異動"
    if workdays == 1:
        return "服務前1天異動"
    return f"服務前{workdays}個工作天異動"


def _workday_note(fee_info: dict) -> str:
    """組成計算依據用的異動時間說明。"""
    return _change_timing_label(fee_info)


def _format_money(amount) -> str:
    try:
        return str(int(round(float(amount or 0))))
    except (TypeError, ValueError):
        return str(amount or "")


def _format_hours(hours) -> str:
    try:
        value = float(hours or 0)
    except (TypeError, ValueError):
        return str(hours or "")
    if value.is_integer():
        return str(int(value))
    return str(value).rstrip("0").rstrip(".")


def _format_change_fee_j(order: dict, change_fee_info: dict) -> str:
    """依客戶付款型態產生 J 欄異動費文案。"""
    timing = _change_timing_label(change_fee_info)
    amount = _format_money(change_fee_info.get("change_fee"))
    if change_fee_info.get("tier") == "free":
        return f"{timing}，免收異動費${amount}"

    if order.get("payway") == "儲值金":
        rate = _format_money(change_fee_info.get("rate_amount"))
        return f"{timing}，收異動費${rate}*時數=${amount}"

    percent = _format_money(change_fee_info.get("rate_percent"))
    return f"{timing}，收{percent}%異動費${amount}"


def _time_change_timing_label(service_date: date, today: date = None) -> str:
    today = today or date.today()
    if service_date and service_date <= today:
        return "當天"
    return "服務前"


def calc_fare(order: dict) -> int:
    """車馬費 = 專員人數 × $100"""
    return order.get("cleaner_count", 0) * 100


def calc_change_fee(order: dict, service_date: date, change_person: int = None,
                     today: date = None) -> dict:
    """
    依「服務日距今工作天數」+「客戶類別（儲值金/一般）」計算異動費。
    change_person / change_hours：若是儲值金客，異動的人數與時數（若未提供，預設用原訂單人數與時數）
    回傳 dict: {workdays, tier, change_fee, calc_note}
    """
    workdays = _count_workdays_before(service_date, today=today)
    if workdays >= 4:
        tier = "free"
    elif workdays <= 1:
        tier = "near"
    else:
        tier = "far"   # near=服務前0-1工作天, far=服務前2-3工作天, free=4工作天以上

    workday_note = _workday_note({"workdays": workdays})

    if order.get("payway") == "儲值金":
        hours = order.get("service_hours", 0)
        person = change_person or order.get("cleaner_count", 0)
        unit = (hours * person) / 2
        rate = 300 if tier == "near" else (200 if tier == "far" else 0)
        change_fee = round(unit * rate)
        if tier == "free":
            calc_note = (
                f"{workday_note}，儲值金客：4個工作天以上，免收異動費 = $0；"
                "若有平日/週末互轉，請另選互轉情境計算每人時差額"
            )
        else:
            calc_note = (
                f"{workday_note}，儲值金客：{hours}小時×{person}人÷2={unit}單位 "
                f"× ${rate}/單位 = ${change_fee}"
            )
        rate_amount = rate
        rate_percent = None
    else:
        rate = 0.5 if tier == "near" else (0.3 if tier == "far" else 0)
        change_fee = round(order.get("total", 0) * rate)
        if tier == "free":
            calc_note = (
                f"{workday_note}，一般客：4個工作天以上，免收異動費 = $0；"
                "若有平日/週末互轉，請另選互轉情境計算每人時差額"
            )
        else:
            calc_note = (
                f"{workday_note}，一般客：總金額{order.get('total', 0)} × "
                f"{int(rate*100)}% = ${change_fee}"
            )
        unit = None
        rate_amount = None
        rate_percent = int(rate * 100)

    return {
        "workdays": workdays,
        "tier": tier,
        "change_fee": change_fee,
        "billing_units": unit,
        "rate_amount": rate_amount,
        "rate_percent": rate_percent,
        "calc_note": calc_note,
    }


def calc_refund_amount(order: dict, change_fee: int) -> int:
    """應退款 = 服務費基礎（總金額 − 車馬費）− 異動費。車馬費不列入退款比例計算。"""
    return max(get_service_amount(order) - _money_int(change_fee), 0)


# ============================================================
# 階段 A-3：組合一筆要寫入 Sheet 的列（三種情境）
# ============================================================

def build_fare_row(order: dict, service_date: date = None, today: date = None) -> dict:
    """車馬費發票"""
    fare = calc_fare(order)
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    return {
        "A": "清潔", "B": "待處理發票", "C": TYPE_FARE,
        "E": _today_taipei_str(today),
        "F": "", "G": order["order_no"], "H": order["customer_name"],
        "I": i_value, "J": f"車馬費 ${fare}",
        "_calc_amount": fare,
    }


def build_charge_row(order: dict, change_fee_info: dict, service_note: str,
                      customer_type: str = "一般", service_date: date = None,
                      today: date = None) -> dict:
    """不退服務 → 異動服務收款（待收款）"""
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    j_value = _format_change_fee_j(order, change_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_CHARGE, "C": TYPE_CHARGE,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value, "J": j_value,
        "K": service_note or "",
        "M": "", "N": change_fee_info["change_fee"], "O": "",
        "_calc_amount": change_fee_info["change_fee"],
        "_calc_note": change_fee_info["calc_note"],
    }


def build_refund_row(order: dict, change_fee_info: dict, service_note: str,
                      customer_type: str = "一般", service_date: date = None,
                      today: date = None) -> dict:
    """退服務 → 異動服務退款（待退款），餘額退還"""
    refund_amount = calc_refund_amount(order, change_fee_info["change_fee"])
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    j_value = _format_change_fee_j(order, change_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_REFUND, "C": TYPE_REFUND,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value,
        "J": j_value,
        "K": service_note or "",
        "R": "信用卡" if order.get("payway") != "儲值金" else "儲值金",
        "S": refund_amount,
        "X": order.get("invoice_no", ""),
        "Y": "三聯" if order.get("carrier_type") == "三聯式" else "二聯",
        "_calc_amount": refund_amount,
        "_refund_amount": refund_amount,
        "_change_fee": change_fee_info.get("change_fee", 0),
        "_travel_fee": get_travel_fee(order),
        "_service_amount": get_service_amount(order),
        "_calc_note": change_fee_info["calc_note"],
    }


# ============================================================
# 階段 A-3b：加時 / 減時（按人時計價，平日／假日不同費率）
# ============================================================

TIME_RATE_WEEKDAY = 600  # 平日每人時
TIME_RATE_WEEKEND = 700  # 週末／例假日每人時
TIME_RATE_DAY_TYPE_DIFF = TIME_RATE_WEEKEND - TIME_RATE_WEEKDAY  # 平日/週末互轉每人時差額


def calc_time_change_fee(service_date: date, hours: float, person: int) -> dict:
    """
    加時／減時金額試算：平日每人時 $600，週末／例假日每人時 $700。
    回傳 dict: {amount, rate, is_weekend, calc_note}
    """
    is_weekend = _is_weekend_or_holiday(service_date) if service_date else False
    rate = TIME_RATE_WEEKEND if is_weekend else TIME_RATE_WEEKDAY
    amount = round((hours or 0) * (person or 0) * rate)
    day_label = "週末/例假日" if is_weekend else "平日"
    calc_note = f"{day_label}：{hours}小時 × {person}人 × ${rate}/人時 = ${amount}"
    return {
        "amount": amount,
        "rate": rate,
        "hours": hours,
        "person": person,
        "is_weekend": is_weekend,
        "calc_note": calc_note,
    }


def calc_flat_person_hour_fee(hours: float, person: int, rate: int, label: str) -> dict:
    """固定每人時費率試算，用於加收／退款差額情境。"""
    amount = round((hours or 0) * (person or 0) * rate)
    calc_note = f"{label}：{hours}小時 × {person}人 × ${rate}/人時 = ${amount}"
    return {
        "amount": amount,
        "rate": rate,
        "hours": hours,
        "person": person,
        "calc_note": calc_note,
    }


def _format_people_hours_fee_j(prefix: str, action: str, time_fee_info: dict) -> str:
    person = _format_hours(time_fee_info.get("person"))
    hours = _format_hours(time_fee_info.get("hours"))
    amount = _format_money(time_fee_info.get("amount"))
    return f"{prefix}{person}人{hours}小時，{action}服務費${amount}"


def build_addtime_row(order: dict, time_fee_info: dict, service_note: str,
                       customer_type: str = "一般", service_date: date = None,
                       today: date = None) -> dict:
    """加時 → 異動服務收款（待收款），其餘欄位結構同異動待收款"""
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    timing = _time_change_timing_label(service_date, today=today)
    j_value = _format_people_hours_fee_j(f"{timing}加時", "待收", time_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_CHARGE, "C": TYPE_CHARGE,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value, "J": j_value,
        "K": service_note or "",
        "M": "", "N": time_fee_info["amount"], "O": "",
        "_calc_amount": time_fee_info["amount"],
        "_calc_note": time_fee_info["calc_note"],
    }


def build_reducetime_row(order: dict, time_fee_info: dict, service_note: str,
                          customer_type: str = "一般", service_date: date = None,
                          today: date = None) -> dict:
    """減時 → 異動服務退款（待退款），其餘欄位結構同異動待退款"""
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    timing = _time_change_timing_label(service_date, today=today)
    j_value = _format_people_hours_fee_j(f"{timing}減時", "待退", time_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_REFUND, "C": TYPE_REFUND,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value,
        "J": j_value,
        "K": service_note or "",
        "R": "信用卡" if order.get("payway") != "儲值金" else "儲值金",
        "S": time_fee_info["amount"],
        "X": order.get("invoice_no", ""),
        "Y": "三聯" if order.get("carrier_type") == "三聯式" else "二聯",
        "_calc_amount": time_fee_info["amount"],
        "_calc_note": time_fee_info["calc_note"],
    }


def build_weekday_to_weekend_row(order: dict, time_fee_info: dict, service_note: str,
                                  customer_type: str = "一般", service_date: date = None,
                                  today: date = None) -> dict:
    """異動平日轉週末 → 待收款，每人時差額 $100。"""
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    j_value = _format_people_hours_fee_j("異動平日轉週末", "待收", time_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_CHARGE, "C": TYPE_CHARGE,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value, "J": j_value,
        "K": service_note or "",
        "M": "", "N": time_fee_info["amount"], "O": "",
        "_calc_amount": time_fee_info["amount"],
        "_calc_note": time_fee_info["calc_note"],
    }


def build_weekend_to_weekday_row(order: dict, time_fee_info: dict, service_note: str,
                                  customer_type: str = "一般", service_date: date = None,
                                  today: date = None) -> dict:
    """異動週末轉平日 → 待退款，每人時差額 $100。"""
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    j_value = _format_people_hours_fee_j("異動週末轉平日", "待退", time_fee_info)
    return {
        "A": "清潔", "B": STATUS_PENDING_REFUND, "C": TYPE_REFUND,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value, "J": j_value,
        "K": service_note or "",
        "R": "信用卡" if order.get("payway") != "儲值金" else "儲值金",
        "S": time_fee_info["amount"],
        "X": order.get("invoice_no", ""),
        "Y": "三聯" if order.get("carrier_type") == "三聯式" else "二聯",
        "_calc_amount": time_fee_info["amount"],
        "_calc_note": time_fee_info["calc_note"],
    }


# ============================================================
# 階段 A-3c：客訴 / 物損退款（金額人工輸入）
# ============================================================

def build_manual_refund_row(order: dict, amount, refund_type_label: str, service_note: str,
                             customer_type: str = "一般", service_date: date = None,
                             today: date = None) -> dict:
    """
    客訴 / 物損退款：金額由人工輸入（沒有固定公式），狀態固定「待退款」，
    其餘欄位結構同異動待退款。refund_type_label 寫入 C 欄（細項），
    例如 TYPE_COMPLAINT_REFUND（客訴退款）或 TYPE_DAMAGE_REFUND（物損退款）。
    """
    amount = round(amount or 0)
    i_value = _format_service_datetime(service_date, order.get("period_text", ""))
    j_value = (
        f"{refund_type_label}，{service_note}，退費 ${amount}"
        if service_note else
        f"{refund_type_label}，退費 ${amount}"
    )
    return {
        "A": "清潔", "B": STATUS_PENDING_REFUND, "C": refund_type_label,
        "E": _today_taipei_str(today),
        "F": customer_type, "G": order["order_no"], "H": order["customer_name"],
        "I": i_value,
        "J": j_value,
        "K": service_note or "",
        "R": "信用卡" if order.get("payway") != "儲值金" else "儲值金",
        "S": amount,
        "X": order.get("invoice_no", ""),
        "Y": "三聯" if order.get("carrier_type") == "三聯式" else "二聯",
        "_calc_amount": amount,
        "_calc_note": f"{refund_type_label}（人工輸入金額）= ${amount}",
    }

def append_rows_to_sheet(region: str, rows: list, ui_logger=None):
    """
    把試算好的列（list of dict，欄位用 A/B/C/...）寫入清潔異動工作表最後一列之後。
    呼叫前請先用 ask/dry-run 讓使用者確認過。
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    ws = get_worksheet(region)

    # 不能用 get_all_values() 的列數判斷起始列：
    # 工作表很多空白列掛了資料驗證下拉選單，即使沒選值，
    # Google Sheets 仍可能把那些列算進「有內容」，導致抓到的列數是整個格線上限（例如 922），
    # 而不是實際資料的最後一列。改成只看 B 欄（狀態）實際有值的最後一列。
    b_values = ws.col_values(2)  # B 欄
    last_data_row = len(b_values)
    while last_data_row > 0 and not b_values[last_data_row - 1].strip():
        last_data_row -= 1
    start_row = last_data_row + 1

    # K 欄為工作表公式：=TEXT(E列,"YYYY/MM/DD")&" "&G列&" "&C列&"--"&J列
    # 程式不可覆蓋 K 欄，避免台北 / 台中公式被清掉。
    col_letters = sorted(set(
        k for row in rows for k in row.keys() if not k.startswith("_") and k != "K"
    ))

    needed_rows = start_row + len(rows) - 1
    if needed_rows > ws.row_count:
        ws.add_rows(needed_rows - ws.row_count)

    written = 0
    errors = []
    for i, row in enumerate(rows):
        target_row = start_row + i
        try:
            for col in col_letters:
                if col in row and row[col] != "":
                    ws.update_acell(f"{col}{target_row}", row[col])
            written += 1
            log(f"✅ 已寫入第 {target_row} 列：{row.get('G', '')}")
        except Exception as e:
            errors.append(f"第 {target_row} 列（{row.get('G','')}）寫入失敗：{e}")

    return {"written": written, "errors": errors, "start_row": start_row}


# ============================================================
# 階段 B：讀取 Sheet 待處理列 → 回填後台
# ============================================================

CHECK_PENDING_CHARGE = ["isCharge"]
CHECK_PENDING_REFUND = ["isRefund"]
CHECK_DONE_CHARGE = [
    "isChargePaid", "isCharged", "chargePaid", "chargeSuccess",
    "isChargeSuccess", "isChargeDone", "chargeDone",
]
CHECK_REFUND_FULL = [
    "isRefundAll", "isFullRefund", "refundAll", "allRefund",
    "refundFull", "fullRefund", "isAllRefund",
]
CHECK_REFUND_PARTIAL = [
    "isRefundPart", "isRefundPartial", "isPartialRefund", "refundPart",
    "refundPartial", "partialRefund", "isPartRefund",
]

FIELD_CHARGE_DATE = ["chargeDate", "charge_date", "addChargeDate", "extraChargeDate"]
FIELD_CHARGE_AMOUNT = ["chargeAmount", "charge_amount", "addChargeAmount", "extraChargeAmount"]
FIELD_CHARGE_INVOICE = ["chargeInvoice", "charge_invoice", "addChargeInvoice"]
FIELD_CHARGE_NOTE = ["chargeNote", "charge_note", "addChargeNote", "extraChargeNote"]

FIELD_REFUND_DATE = ["refundDate", "refund_date"]
FIELD_REFUND_AMOUNT = ["refundAmount", "refund_amount"]
FIELD_REFUND_NUMBER = ["refundNumber", "refund_number", "refundNo", "refund_no"]
FIELD_REFUND_FLOW = [
    "refundPayway", "refund_payway", "refundPayment", "refund_payment",
    "refundMethod", "refund_method", "refundWay", "refund_way",
]
FIELD_REFUND_NOTE = ["refundNote", "refund_note"]
FIELD_FINANCE_NOTE = [
    "financeNote", "finance_note", "financialNote", "financial_note",
    "accountingNote", "accounting_note", "paymentNote", "payment_note",
    "moneyNote", "money_note", "financeMemo", "finance_memo",
]


def _sheet_cell(row: list, letter: str) -> str:
    idx = _col_letter_to_index(letter)
    return row[idx] if len(row) > idx else ""


def _parse_money_value(value) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    text = re.sub(r"[^\d.-]", "", text)
    if not text:
        return 0
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def _normalize_date_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not m:
        return text
    y, mo, d = map(int, m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _control_context(el) -> str:
    parts = []
    el_id = el.get("id")
    if el_id:
        form = el.find_parent("form")
        label = form.find("label", attrs={"for": el_id}) if form else None
        if label:
            parts.append(label.get_text(" ", strip=True))

    parent_label = el.find_parent("label")
    if parent_label:
        parts.append(parent_label.get_text(" ", strip=True))

    for sibling in (el.find_previous_sibling(), el.find_next_sibling()):
        if sibling and sibling.name == "label":
            parts.append(sibling.get_text(" ", strip=True))

    parent = el
    for _ in range(4):
        parent = parent.find_parent()
        if not parent or parent.name == "form":
            break
        if parent.name in ("div", "tr", "td", "li", "label", "section"):
            text = parent.get_text(" ", strip=True)
            if text:
                parts.append(text)

    return " ".join(dict.fromkeys(parts))


def _read_form_state(soup: BeautifulSoup) -> tuple[dict, dict]:
    """
    讀取後台表單現值。checkbox/radio 只有原本 checked 的才放入 form_data，
    這樣後續移除某個 checkbox 欄位時，POST 行為才會等同「取消勾選」。
    """
    form_data = {}
    controls = {}

    for el in soup.select("form input[name], form textarea[name], form select[name]"):
        name = el.get("name")
        if not name or name == "_token":
            continue

        tag = el.name.lower()
        input_type = (el.get("type") or tag).lower()
        if tag == "textarea":
            value = el.get_text()
        elif tag == "select":
            chosen = el.select_one("option[selected]")
            value = chosen.get("value", "") if chosen else ""
        else:
            value = el.get("value", "")

        control = {
            "name": name,
            "tag": tag,
            "type": input_type,
            "value": value,
            "checked": el.has_attr("checked"),
            "context": _control_context(el),
        }
        controls.setdefault(name, []).append(control)

        if input_type in ("checkbox", "radio"):
            if control["checked"]:
                form_data[name] = value or "1"
        else:
            form_data[name] = value

    return form_data, controls


def _find_control(controls: dict, names: list, keywords: list = None,
                  allowed_types: tuple = None):
    for name in names:
        if name in controls:
            for control in controls[name]:
                if not allowed_types or control["type"] in allowed_types:
                    return name, control
            return name, controls[name][0]

    if not keywords:
        return None, None

    for name, name_controls in controls.items():
        for control in name_controls:
            if allowed_types and control["type"] not in allowed_types:
                continue
            context = control.get("context", "")
            if any(keyword in context for keyword in keywords):
                return name, control

    return None, None


def _unchecked_value(controls: dict, name: str) -> Optional[str]:
    for control in controls.get(name, []):
        if control["type"] == "hidden":
            return control.get("value", "")
    return None


def _set_checkbox(form_data: dict, controls: dict, names: list, checked: bool,
                  keywords: list = None, fallback_name: str = None,
                  ui_logger=None):
    name, control = _find_control(
        controls, names, keywords=keywords, allowed_types=("checkbox", "radio")
    )
    if not name and fallback_name:
        name = fallback_name
        control = {"value": "1"}
    if not name:
        if ui_logger:
            ui_logger(f"⚠️ 找不到 checkbox 欄位：{keywords or names}")
        return None

    if checked:
        form_data[name] = (control or {}).get("value") or "1"
    else:
        hidden_value = _unchecked_value(controls, name)
        if hidden_value is None:
            form_data.pop(name, None)
        else:
            form_data[name] = hidden_value
    return name


def _set_radio_value(form_data: dict, controls: dict, name: str, value, ui_logger=None):
    """直接設定 radio 欄位值。後台 isCharge / isRefund 是同名 radio，不可用 checkbox 勾選邏輯。"""
    value = str(value)
    if name in controls:
        allowed = [str(c.get("value", "")) for c in controls.get(name, [])]
        if value not in allowed and ui_logger:
            ui_logger(f"⚠️ {name} 找不到選項值 {value}，可用值：{allowed}")
    form_data[name] = value
    return name


def _append_suffix_once(text: str, suffix: str) -> str:
    text = str(text or "").strip()
    suffix = str(suffix or "").strip()
    if not suffix:
        return text
    if suffix in text:
        return text
    return f"{text}{suffix}" if text else suffix.lstrip("，, ")


def _first_line_for_finance(note: str) -> str:
    return str(note or "").strip().splitlines()[0].strip() if str(note or "").strip() else ""


def _set_field(form_data: dict, controls: dict, names: list, value,
               keywords: list = None, fallback_name: str = None,
               allow_blank: bool = False, ui_logger=None):
    if value is None:
        return None
    value = str(value)
    if not value and not allow_blank:
        return None

    name, _control = _find_control(controls, names, keywords=keywords)
    if not name and fallback_name:
        name = fallback_name
    if not name:
        if ui_logger:
            ui_logger(f"⚠️ 找不到欄位：{keywords or names}")
        return None

    form_data[name] = value
    return name


def _prepend_field(form_data: dict, controls: dict, names: list, note: str,
                   keywords: list = None, ui_logger=None):
    note = str(note or "").strip()
    if not note:
        return None

    name, _control = _find_control(controls, names, keywords=keywords)
    if not name:
        if ui_logger:
            ui_logger("⚠️ 找不到財務備註欄位，略過財務備註貼入")
        return None

    original = str(form_data.get(name, "") or "")
    if original.strip().startswith(note):
        form_data[name] = original
    elif original:
        form_data[name] = f"{note}\n{original}"
    else:
        form_data[name] = note
    return name


def _row_kind(status: str) -> str:
    if status in (STATUS_PENDING_CHARGE, STATUS_DONE_CHARGE):
        return "charge"
    if status in (STATUS_PENDING_REFUND, STATUS_DONE_REFUND):
        return "refund"
    return ""


def _row_amount(row: list, status: str) -> str:
    if _row_kind(status) == "charge":
        return _sheet_cell(row, "N")
    if _row_kind(status) == "refund":
        return _sheet_cell(row, "S")
    return ""



def _parse_sheet_row_spec(row_spec: str) -> set[int]:
    """解析 Sheet 列號字串，支援：19、19,21、19-22、19，21。"""
    text = str(row_spec or "").strip()
    if not text:
        return set()

    rows = set()
    for part in re.split(r"[,，\s]+", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                start, end = end, start
            rows.update(range(start, end + 1))
            continue
        if re.fullmatch(r"\d+", part):
            rows.add(int(part))
            continue
        raise ValueError(f"列號格式錯誤：{part}（支援：19、19,21、19-22）")
    return rows

def get_pending_rows(region: str, row_spec: str = None, ui_logger=None):
    """
    讀取清潔異動工作表，篩出需要回填後台的列。
    支援 B 欄狀態：待收款、待退款、已收款、已退款；且對應金額欄需有值。
    回傳 list of dict，含 sheet_row（原始列號，回寫用）。
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    wanted_rows = _parse_sheet_row_spec(row_spec) if row_spec else set()

    ws = get_worksheet(region)
    all_values = ws.get_all_values()

    pending = []
    for row_no, row in enumerate(all_values[1:], start=2):
        if wanted_rows and row_no not in wanted_rows:
            continue
        if len(row) < 2:
            continue
        status = _sheet_cell(row, "B").strip()
        order_no = _sheet_cell(row, "G").strip()
        if not order_no:
            continue
        if status not in SYNC_STATUSES:
            continue

        amount = _row_amount(row, status)
        if not amount:
            continue

        kind = _row_kind(status)
        pending.append({
            "sheet_row": row_no,
            "kind": kind,
            "status": status,
            "order_no": order_no,
            "customer_name": _sheet_cell(row, "H"),
            "j_note": _sheet_cell(row, "J"),
            "k_note": _sheet_cell(row, "K"),
            "refund_invoice_type": _sheet_cell(row, "Y") if kind == "refund" else "",
            "amount": amount,
            "raw": row,
        })

    log(f"掃描到 {len(pending)} 筆待回填資料" + (f"（指定列號：{row_spec}）" if row_spec else ""))
    return pending


def apply_sheet_row_to_form(form_data: dict, controls: dict, item: dict,
                            order: dict = None, ui_logger=None):
    """依 Sheet B 欄狀態，把該列資料套到後台 edit 表單。"""
    raw = item["raw"]
    status = item.get("status") or _sheet_cell(raw, "B").strip()
    backend_note = _sheet_cell(raw, "K").strip()
    charge_date = _normalize_date_value(_sheet_cell(raw, "M"))
    charge_invoice = _sheet_cell(raw, "O").strip()
    refund_date = _normalize_date_value(_sheet_cell(raw, "AC"))

    if status == STATUS_PENDING_CHARGE:
        _set_radio_value(form_data, controls, "isCharge", "1", ui_logger=ui_logger)
        _set_radio_value(form_data, controls, "isRefund", "0", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_DATE, charge_date,
                   keywords=["加收日期", "收款日期", "收款時間"], fallback_name="chargeDate", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_AMOUNT, _sheet_cell(raw, "N"),
                   keywords=["加收金額", "收款金額"], fallback_name="chargeAmount", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_INVOICE, charge_invoice,
                   keywords=["加收發票", "收款發票"], fallback_name="chargeInvoice", ui_logger=ui_logger)
        charge_note = _append_suffix_once(backend_note, f"，開立發票{charge_invoice}" if charge_invoice else "")
        _set_field(form_data, controls, FIELD_CHARGE_NOTE, charge_note,
                   keywords=["加收備註", "收款備註"], fallback_name="chargeNote", ui_logger=ui_logger)
        finance_line = _first_line_for_finance(charge_note)
        if finance_line:
            _prepend_field(form_data, controls, FIELD_FINANCE_NOTE, finance_line,
                           keywords=["財務備註"], ui_logger=ui_logger)
        return

    if status == STATUS_PENDING_REFUND:
        _set_radio_value(form_data, controls, "isCharge", "0", ui_logger=ui_logger)
        _set_radio_value(form_data, controls, "isRefund", "1", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_DATE, refund_date,
                   keywords=["退款日期", "退款時間"], fallback_name="refundDate", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_AMOUNT, _sheet_cell(raw, "S"),
                   keywords=["退款金額"], fallback_name="refundAmount", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_FLOW, _sheet_cell(raw, "R"),
                   keywords=["退款金流"], ui_logger=ui_logger)
        refund_note = _append_suffix_once(backend_note, f"，{refund_date}已退款" if refund_date else "")
        _set_field(form_data, controls, FIELD_REFUND_NOTE, refund_note,
                   keywords=["待退備註", "退款備註"], fallback_name="refundNote", ui_logger=ui_logger)
        finance_line = _first_line_for_finance(refund_note)
        if finance_line:
            _prepend_field(form_data, controls, FIELD_FINANCE_NOTE, finance_line,
                           keywords=["財務備註"], ui_logger=ui_logger)
        return

    if status == STATUS_DONE_CHARGE:
        _set_radio_value(form_data, controls, "isCharge", "2", ui_logger=ui_logger)
        _set_radio_value(form_data, controls, "isRefund", "0", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_DATE, charge_date,
                   keywords=["加收日期", "收款日期", "收款時間"], fallback_name="chargeDate", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_AMOUNT, _sheet_cell(raw, "N"),
                   keywords=["加收金額", "收款金額"], fallback_name="chargeAmount", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_CHARGE_INVOICE, charge_invoice,
                   keywords=["加收發票", "收款發票"], fallback_name="chargeInvoice", ui_logger=ui_logger)
        charge_note = _append_suffix_once(backend_note, f"，開立發票{charge_invoice}" if charge_invoice else "")
        _set_field(form_data, controls, FIELD_CHARGE_NOTE, charge_note,
                   keywords=["加收備註", "收款備註"], fallback_name="chargeNote", ui_logger=ui_logger)
        finance_line = _first_line_for_finance(charge_note)
        if finance_line:
            _prepend_field(form_data, controls, FIELD_FINANCE_NOTE, finance_line,
                           keywords=["財務備註"], ui_logger=ui_logger)
        return

    if status == STATUS_DONE_REFUND:
        _set_radio_value(form_data, controls, "isCharge", "0", ui_logger=ui_logger)

        refund_amount = _parse_money_value(_sheet_cell(raw, "S"))
        order_total = _parse_money_value((order or {}).get("total"))
        is_full_refund = bool(order_total and refund_amount == order_total)
        _set_radio_value(form_data, controls, "isRefund", "3" if is_full_refund else "2", ui_logger=ui_logger)

        _set_field(form_data, controls, FIELD_REFUND_DATE, refund_date,
                   keywords=["退款日期", "退款時間"], fallback_name="refundDate", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_AMOUNT, _sheet_cell(raw, "S"),
                   keywords=["退款金額"], fallback_name="refundAmount", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_NUMBER, _sheet_cell(raw, "AB"),
                   keywords=["折讓單號碼", "退款編號"], fallback_name="refundNumber", ui_logger=ui_logger)
        _set_field(form_data, controls, FIELD_REFUND_FLOW, _sheet_cell(raw, "R"),
                   keywords=["退款金流"], ui_logger=ui_logger)
        refund_note = _append_suffix_once(backend_note, f"，{refund_date}已退款" if refund_date else "")
        _set_field(form_data, controls, FIELD_REFUND_NOTE, refund_note,
                   keywords=["待退備註", "退款備註"], fallback_name="refundNote", ui_logger=ui_logger)
        finance_line = _first_line_for_finance(refund_note)
        if finance_line:
            _prepend_field(form_data, controls, FIELD_FINANCE_NOTE, finance_line,
                           keywords=["財務備註"], ui_logger=ui_logger)
        return

    raise RuntimeError(f"不支援的 B 欄狀態：{status}")


def sync_one_to_purchase_edit(item: dict, session: requests.Session, ui_logger=None):
    """
    把清潔異動工作表單列 POST 回 purchase/edit/{purchase_id}。
    需要先用訂單編號找到 purchase_id（用 fetch_order_basic 取得）。
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    order_no = item["order_no"]
    order = fetch_order_basic(order_no, session=session, ui_logger=ui_logger, by="orderNo")
    if not order or not order.get("purchase_id"):
        raise RuntimeError(f"找不到訂單 {order_no} 對應的 purchase_id")

    purchase_id = order["purchase_id"]
    edit_url = f"{BASE_URL}/purchase/edit/{purchase_id}"

    # 取得編輯頁，拿出原本所有欄位值 + CSRF token，原樣帶回去（避免覆蓋掉沒提到的欄位）
    resp = session.get(edit_url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    token_input = soup.select_one('input[name="_token"]')
    token = token_input["value"] if token_input else ""

    form_data, controls = _read_form_state(soup)
    apply_sheet_row_to_form(form_data, controls, item, order=order, ui_logger=ui_logger)
    form_data["_token"] = token

    log(f"回填 {order_no}（purchase_id={purchase_id}，Sheet B={item.get('status')}）")
    post_resp = session.post(edit_url, data=form_data, timeout=20)
    post_resp.raise_for_status()
    log(f"✅ {order_no} 回填完成")
    return True


def mark_sheet_row_done(region: str, sheet_row: int, kind: str, ui_logger=None):
    """回填成功後只標記處理時間，不改 B 欄狀態。"""
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    ws = get_worksheet(region)
    ws.update_acell(f"AD{sheet_row}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log(f"✅ Sheet 第 {sheet_row} 列已標記系統回填時間（B 欄狀態不變）")


# ============================================================
# 階段 B 主流程
# ============================================================

def sync_pending_rows(region: str, selected_rows: list, session: requests.Session,
                       ui_logger=None):
    """
    selected_rows: get_pending_rows() 回傳結果中，使用者勾選要執行的項目
    """
    def log(msg):
        if ui_logger:
            ui_logger(msg)

    result = {"processed": 0, "success": 0, "failed": 0, "errors": []}

    for item in selected_rows:
        result["processed"] += 1
        try:
            sync_one_to_purchase_edit(item, session=session, ui_logger=ui_logger)
            mark_sheet_row_done(region, item["sheet_row"], item["kind"], ui_logger=ui_logger)
            result["success"] += 1
        except Exception as e:
            result["failed"] += 1
            result["errors"].append(f"{item['order_no']}：{e}")
            log(f"❌ {item['order_no']} 失敗：{e}")

    return result
