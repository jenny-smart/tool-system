# ============================================================
# 檔名：orders.py
# 版本：v2026.07.07
# 模組：批次建單核心引擎（Google Sheet → 後台訂單，供 ordersapp.py 呼叫）
# 最後更新：2026-07-07
#
# Change Log
# v2026.07.07
# - 修正 ORDER_NO_REGEX 只認得 LC/TT 開頭的訂單編號，導致這次發現的「儲值金
#   購買」訂單（KK 開頭，例如 KK00212122）完全沒被辨識成一張訂單卡片的起點，
#   在 extract_order_cards_from_purchase_html 解析訂單列表時整張漏掉或跟
#   前後訂單卡片的內容混在一起。這個函式是整個系統解析後台訂單列表的共用
#   基礎（批次、LINE 訊息、發票/付款方式查詢、一致性檢查全部都靠它），影響
#   範圍很廣。修法：改成明確列舉前綴 (LC|TT|KK)。
#   　※有考慮過改成更寬鬆的「任兩個大寫字母＋數字」規則，但發票號碼（例如
#   　　TF27826169、FG82592263）剛好也符合這個格式，會被誤判成訂單編號，
#   　　反而把訂單卡片切錯位置，所以還是採用明確列舉前綴的做法，比較安全。
#   　　之後如果後台又出現新的訂單編號前綴，記得要加進這個清單。
# v2026.07.06
# - 新增 PURCHASE_FILTER_PARAMS_TEMPLATE，並修正 verify_batch_order_consistency
#   查詢 /purchase 時原本只送 {"orderNo": ...} 或 {"phone": ...} 單一參數，
#   跟後台搜尋表單瀏覽器實際送出的參數（所有欄位都會帶上，只是空字串）不同，
#   可能觸發後台不同的預設篩選邏輯，導致查到的結果比預期少。現在統一以完整
#   樣板為底，只覆蓋真正要篩選的欄位（配合 quick_order.py v8.23 同步修正）。
# v2026.07.05
# - 新增 run_batch_consistency_check：把一致性檢查從 run_process_web 內部抽出來，
#   改成獨立函式，只在「整批列都執行完」之後由 ordersapp.py 呼叫一次，而不是
#   原本每呼叫一次 run_process_web（每一列）就各自比對一次——原寫法會讓同一支
#   電話在多列批次裡被重複查詢很多次，也不是真正「全部成單到一個段落後」的
#   整批核對。run_process_web 不再自動觸發一致性檢查。
# - verify_batch_order_consistency 擴充為雙向比對：
#   方向一（從 Google Sheet 比對系統）：原本只比對電話/日期/時段，這次加入
#   地址比對（依電話/地址/日期/時間四項），更嚴謹地確認訂單編號沒有誤配對。
#   方向二（新增，從系統的日期區間比對 Google Sheet）：以這批次涉及的每支
#   電話，查詢系統該電話底下落在這批次日期範圍內的實際訂單，確認每一筆都能
#   對應回 Google Sheet 某一列寫下的訂單編號，抓出「系統其實已經成單，但
#   Google Sheet 沒有正確記錄（M欄空白或寫錯）」這種方向一照不到的死角。
# v2026.07.04
# - 新增檸檬人排班工具函式（VALUE_TO_SHIFT_CODE / ensure_lemon_cleaner_shifts 等，
#   邏輯與 quick_order.py 一致），process_one_group 新增 allow_auto_lemon_shift
#   參數（預設 False）：查無班表時，只有客服明確勾選才會自動補檸檬人排班，
#   不再查不到班表就自動嘗試。run_process_web / run_process 皆已貫穿此參數，
#   讓「批次」跟「舊客/新客/訂單轉換/儲值金補價差」五個成單功能共用同一套邏輯。
# - 修正 fetch_order_no_by_date_and_period / match_order_from_purchase_page：
#   原本只比對「日期＋時段」，未比對電話，導致同一天同時段有多筆不同客人訂單時，
#   可能誤配對到別人的訂單編號，造成 Google Sheet 訂單編號欄（M欄）重複、
#   實際上這一列並沒有真的成單。現在改為同時比對電話，並排除本次批次已用過的
#   訂單編號。
# - 新增 verify_batch_order_consistency：批次執行完畢、回填 Google Sheet 後，
#   自動逐列比對「電話、日期、時段」是否跟寫回的訂單編號實際對應的後台訂單一致，
#   抓出訂單編號誤配對、重複寫入、或該列其實沒有真的成單的情況，結果會透過
#   run_process_web 回傳的 consistency_problems 提供給 ordersapp.py 顯示。
# 開發歷史（此版本之前無版本標示紀錄，檔案主體邏輯延續既有「儲值金系統設定」）：
# - 原始檔案標示：儲值金系統設定.py 版本：2026-05-03-final-staff-notice-aa
# ============================================================
# -*- coding: utf-8 -*-
import os
import re
import json
import time
import html
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import pandas as pd
from bs4 import BeautifulSoup

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .accounts import ACCOUNTS
from .env import (
    ENV,
    BASE_URL_DEV,
    BASE_URL_PROD,
    GOOGLE_SHEET_ID,
    ENABLE_GCAL_COLOR_SYNC,
    GOOGLE_CALENDAR_MAP,
    GOOGLE_SERVICE_ACCOUNT_FILE,
    COLOR_PURPLE,
    COLOR_YELLOW,
    REQUEST_DELAY,
    ORDER_PREFIX_DEV,
    ORDER_PREFIX_PROD,
)

try:
    import streamlit as st
except Exception:
    st = None

try:
    from env import GOOGLE_MAPS_API_KEY
except Exception:
    GOOGLE_MAPS_API_KEY = ""


# =========================
# 環境
# =========================
if ENV == "dev":
    BASE_URL = BASE_URL_DEV
    ORDER_PREFIX = ORDER_PREFIX_DEV
else:
    BASE_URL = BASE_URL_PROD
    ORDER_PREFIX = ORDER_PREFIX_PROD

LOGIN_URL = f"{BASE_URL}/login"
BOOKING_URL = f"{BASE_URL}/booking/stored_value_routine"
PURCHASE_URL = f"{BASE_URL}/purchase"
GET_MEMBER_URL = f"{BASE_URL}/ajax/get_member"
CHECK_CONTAIN_URL = f"{BASE_URL}/ajax/check_contain"
CALCULATE_HOUR_URL = f"{BASE_URL}/ajax/calculate_hour"
GET_SECTION_URL = f"{BASE_URL}/ajax/get_section"
MAIL_SUCCESS_URL = f"{BASE_URL}/purchase/mail_success/{{order_no}}"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# v2026.07.05：後台 /purchase 訂單列表頁的搜尋表單，瀏覽器送出時會帶上全部
# 欄位（沒填的欄位是空字串，不是完全不送）。如果我們用 requests 查詢時只帶
# 想篩選的那一兩個參數（例如只送 phone），後台某些邏輯是用「這個參數有沒有
# 出現在請求裡」而不是「值是不是空字串」來判斷，可能會觸發跟瀏覽器不一樣的
# 預設篩選（例如自動加上當月日期區間），導致查到的結果變少甚至查無資料。
# 所以查詢時一律以這份樣板為底，只覆蓋真正要篩選的欄位，其餘保持空字串。
PURCHASE_FILTER_PARAMS_TEMPLATE = {
    "keyword": "", "name": "", "phone": "", "orderNo": "",
    "date_s": "", "date_e": "", "clean_date_s": "", "clean_date_e": "",
    "paid_at_s": "", "paid_at_e": "", "refundDateS": "", "refundDateE": "",
    "buy": "", "area_id": "", "isCharge": "", "isRefund": "",
    "payway": "", "purchase_status": "", "progress_status": "",
    "invoiceStatus": "", "otherFee": "", "orderBy": "",
}
MAIL_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
    "Referer": PURCHASE_URL,
}

CLEAN_TYPE_MAP = {
    "居家清潔": "1",
    "辦公室清潔": "2",
    "裝修細清": "3",
}

# v2026.07.06：訂單編號目前已知前綴有 LC（一般訂單）、TT（測試站/儲值金折抵
# 消費訂單）、KK（儲值金購買訂單，這次才發現）。之前只認 LC/TT，導致 KK 開頭
# 的訂單完全沒被辨識成一張訂單卡片的起點，解析會出錯或漏掉整張訂單。
# 沒有改成更寬鬆的「任兩個大寫字母＋數字」，是因為發票號碼（例如
# TF27826169、FG82592263）剛好也符合這個格式，會被誤判成訂單編號，反而把
# 訂單卡片切錯位置。如果之後後台又出現新的訂單編號前綴，要記得加在這裡。
ORDER_NO_REGEX = r"(LC|TT|KK)\d+"

# 保留舊版可穩定比對班表的系統時段
STANDARD_SLOTS = [
    "08:30-12:30",
    "09:00-11:00",
    "09:00-12:00",
    "14:00-16:00",
    "14:00-17:00",
    "14:00-18:00",
    "09:00-16:00",
    "09:00-18:00",
]

KNOWN_SERVICE_STATUS = [
    "已處理",
    "未處理",
    "處理中",
    "已完成",
    "已取消",
    "待處理",
]

print("=== 儲值金系統設定.py 版本：2026-05-03-final-staff-notice-aa ===")


# =========================
# 基本工具
# =========================
def is_blank(value):
    return str(value).strip() in ("", "nan", "None")


def normalize_phone(phone_value):
    phone = str(phone_value).strip().replace(".0", "")
    phone = re.sub(r"\D", "", phone)
    if len(phone) == 9:
        phone = "0" + phone
    return phone


def normalize_text_for_parse(text):
    return re.sub(r"\s+", "", str(text or ""))


def normalize_addr_for_match(addr):
    return re.sub(r"\s+", "", str(addr or "")).strip()


def same_address(a, b):
    return normalize_addr_for_match(a) == normalize_addr_for_match(b)


def first_nonzero(*values, default="0"):
    for value in values:
        text = str(value if value is not None else "").strip()
        if text not in ("", "0", "0.0", "nan", "None"):
            return text
    return str(default)


def find_nested_value(obj, keys):
    key_set = {str(k) for k in keys}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key) in key_set and value not in (None, ""):
                return value

        for value in obj.values():
            found = find_nested_value(value, key_set)
            if found not in (None, ""):
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = find_nested_value(item, key_set)
            if found not in (None, ""):
                return found

    return ""


def parse_date_value(date_value):
    if isinstance(date_value, pd.Timestamp):
        return date_value.to_pydatetime()

    text = str(date_value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    raise Exception(f"無法解析日期: {date_value}")


def get_date_str(date_value):
    return parse_date_value(date_value).strftime("%Y-%m-%d")


def normalize_sheet_date(date_value):
    return get_date_str(date_value)


def is_weekend(date_value):
    return parse_date_value(date_value).weekday() >= 5


def get_unit_price_by_date(date_value):
    return 700 if is_weekend(date_value) else 600


def parse_time_slot(start_time_str, end_time_str):
    if not str(start_time_str).strip() or not str(end_time_str).strip():
        raise Exception(f"開始時間或結束時間為空：{start_time_str} / {end_time_str}")

    def to_hm(t):
        text = str(t).strip()
        parts = text.split(":")
        if not parts or not parts[0].strip():
            raise Exception(f"時間格式錯誤：{t}")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
        return h, m

    sh, sm = to_hm(start_time_str)
    eh, em = to_hm(end_time_str)
    return sh, sm, eh, em


def calc_hours_from_time(start_time_str, end_time_str):
    sh, sm, eh, em = parse_time_slot(start_time_str, end_time_str)
    hours = (eh - sh) + (em - sm) / 60.0
    return hours if hours > 0 else None


def calc_effective_hours_from_time(start_time_str, end_time_str):
    hours = calc_hours_from_time(start_time_str, end_time_str)
    if hours is None:
        return None
    if hours >= 7:
        hours -= 1
    return hours


def normalize_period_text(start_time_str, end_time_str):
    sh, sm, eh, em = parse_time_slot(start_time_str, end_time_str)
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


def display_period_text(start_time_str, end_time_str):
    sh, sm, eh, em = parse_time_slot(start_time_str, end_time_str)
    return f"{sh:02d}:{sm:02d} - {eh:02d}:{em:02d}"


def normalize_sheet_period(start_time_str, end_time_str):
    return normalize_period_text(start_time_str, end_time_str)


def build_target_slot_from_row(row):
    date_part = normalize_sheet_date(row["日期"])
    period_part = normalize_sheet_period(row["開始時間"], row["結束時間"])
    return f"{date_part}_{period_part}"


def slot_duration_hours(slot_text):
    start_text, end_text = slot_text.split("-")
    return calc_effective_hours_from_time(start_text, end_text)


def slot_start_hour(slot_text):
    return int(slot_text.split("-")[0].split(":")[0])


def is_morning_slot(slot_text):
    return slot_start_hour(slot_text) < 12


def map_to_system_slot(start_time_str, end_time_str, service_text=None):
    """
    重要規則：
    1. Google Sheet 的開始/結束時間 = 客戶實際要約的服務時段，也用來查班表。
       例如 Sheet 是 09:00-12:00，就一定查 09:00-12:00。
    2. calculate_hour 回傳的 hour 只用來算價格，不用來反推班表時段。
    3. 只有特殊時段 10:00-12:00，要送系統 09:00-11:00，並在簡訊/客備註記原始時間。
    """
    original_slot = normalize_period_text(start_time_str, end_time_str)

    if original_slot == "10:00-12:00":
        return {
            "original_slot": original_slot,
            "system_slot": "09:00-11:00",
            "need_note": True,
            "sms_time": original_slot,
            "customer_time_note": f"服務時間：{original_slot}",
        }

    # 標準時段直接用 Sheet 原始時段，不用 hour 反推
    if original_slot in STANDARD_SLOTS:
        return {
            "original_slot": original_slot,
            "system_slot": original_slot,
            "need_note": False,
            "sms_time": "",
            "customer_time_note": "",
        }

    # 非標準時段才用服務時數對應系統可送時段
    actual_hours = None

    if service_text and str(service_text).strip():
        match = re.search(r"(\d+)\s*人\s*(\d+(?:\.\d+)?)\s*小時", str(service_text))
        if match:
            actual_hours = float(match.group(2))
        else:
            match = re.search(r"(\d+(?:\.\d+)?)\s*小時", str(service_text))
            if match:
                actual_hours = float(match.group(1))

    if actual_hours is None:
        actual_hours = calc_effective_hours_from_time(start_time_str, end_time_str)

    if actual_hours is None:
        raise Exception(f"無法解析服務時段: {start_time_str}-{end_time_str}")

    sh, sm, eh, em = parse_time_slot(start_time_str, end_time_str)
    original_is_morning = sh < 12

    matched_slot = None
    for slot in STANDARD_SLOTS:
        if is_morning_slot(slot) == original_is_morning and abs(slot_duration_hours(slot) - actual_hours) < 1e-9:
            matched_slot = slot
            break

    if not matched_slot:
        raise Exception(f"找不到可對應的系統時段：原始時段 {original_slot}，時數 {actual_hours}")

    return {
        "original_slot": original_slot,
        "system_slot": matched_slot,
        "need_note": True,
        "sms_time": original_slot,
        "customer_time_note": f"服務時間：{original_slot}",
    }


def parse_service_human_hour(service_text, start_time, end_time):
    """
    最終規則：
    1. 預設 2 人。
    2. 預設時數 = Google Sheet 開始/結束時間換算。
    3. 若 A欄/服務人時 有明確寫「3人4小時」，則人數與時數都以 A欄為準。
    """
    people = 2
    hours = calc_effective_hours_from_time(start_time, end_time)

    if service_text and str(service_text).strip():
        text = str(service_text).strip()

        people_match = re.search(r"(\d+)\s*人", text)
        if people_match:
            people = int(people_match.group(1))

        hour_match = re.search(r"(\d+(?:\.\d+)?)\s*小時", text)
        if hour_match:
            hours = float(hour_match.group(1))

    if hours is None:
        return people, None

    return people, int(hours) if float(hours).is_integer() else hours


def normalize_hours_text(cell_value, start_time_str=None, end_time_str=None):
    people, hours = parse_service_human_hour(cell_value, start_time_str, end_time_str)
    if hours is None:
        return f"{people}人"
    htxt = f"{int(hours)}小時" if float(hours).is_integer() else f"{hours}小時"
    return f"{people}人{htxt}"


def build_group_key(row):
    normalized_human_hour = normalize_hours_text(
        row["服務人時"],
        row["開始時間"],
        row["結束時間"],
    )
    return (
        str(row["姓名"]).strip(),
        normalize_phone(row["電話"]),
        str(row["地址"]).strip(),
        str(row["購買項目"]).strip(),
        normalize_period_text(row["開始時間"], row["結束時間"]),
        normalized_human_hour,
        str(row["備註"]).strip(),
    )


def get_region_by_address(address, accounts_config):
    for region, config in accounts_config.items():
        keywords = config.get("address_keywords", [])
        if keywords:
            for kw in keywords:
                if kw in address:
                    return region
        else:
            if region == "台北" and ("台北市" in address or "新北市" in address):
                return region
            if region == "台中" and "台中市" in address:
                return region
            if region == "桃園" and "桃園" in address:
                return region
            if region == "新竹" and ("新竹市" in address or "新竹縣" in address):
                return region
            if region == "高雄" and ("高雄市" in address or "台南市" in address):
                return region
    return None


def should_process_row(row):
    return str(row.get("狀態", "")).strip() == "未安排" and is_blank(row.get("訂單編號", ""))


def should_create_order(row):
    return str(row.get("狀態", "")).strip() == "未安排" and is_blank(row.get("訂單編號", ""))


# =========================
# XYZ / 回填模板
# =========================
def finalize_xyz(meta=None, fallback_fare="0"):
    meta = meta or {}

    staff_raw = str(meta.get("服務人員", "") or "").strip()
    staff = normalize_staff_display(staff_raw) if staff_raw else ""
    status = str(meta.get("服務狀態", "") or "").strip()
    fare = str(meta.get("車馬費", "") or "").strip()

    if not staff:
        staff = "無人力"
    if not status:
        status = "未處理"
    if not fare:
        fare = str(fallback_fare or "0").strip() or "0"

    return {
        "服務人員": staff,
        "服務狀態": status,
        "車馬費": fare,
    }


def build_row_result(
    order_no="",
    result="失敗",
    reason="",
    no_slot_date="",
    insufficient_date="",
    sms_time="",
    customer_note="",
    service_notice="",
    confirm_mail="",
    calendar_result="",
    calendar_reason="",
    calendar_old="",
    calendar_new="",
    status_value="",
    staff="無人力",
    service_status="未處理",
    fare="0",
):
    xyz = finalize_xyz(
        {
            "服務人員": staff,
            "服務狀態": service_status,
            "車馬費": fare,
        },
        fallback_fare=fare or "0",
    )

    return {
        "訂單編號": order_no,
        "結果": result,
        "原因": reason,
        "沒班表日期": no_slot_date,
        "餘額不足未送": insufficient_date,
        "簡訊實際服務時間": sms_time,
        "客人備註": customer_note,
        "客服備註": service_notice,
        "確認信": confirm_mail,
        "日曆改色結果": calendar_result,
        "日曆改色原因": calendar_reason,
        "日曆原色": calendar_old,
        "日曆新色": calendar_new,
        "狀態": status_value,
        "服務人員": xyz["服務人員"],
        "服務狀態": xyz["服務狀態"],
        "車馬費": xyz["車馬費"],
    }


# =========================
# Google 憑證 / Sheet
# =========================
def get_service_account_info():
    if st is not None:
        try:
            if "gcp_service_account" in st.secrets:
                return dict(st.secrets["gcp_service_account"])
            if "GOOGLE_SERVICE_ACCOUNT" in st.secrets:
                return dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        except Exception:
            pass

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        try:
            return json.loads(raw_json)
        except Exception as e:
            raise Exception(f"GOOGLE_SERVICE_ACCOUNT_JSON 不是合法 JSON：{e}")

    candidate_files = []
    if GOOGLE_SERVICE_ACCOUNT_FILE:
        candidate_files.append(GOOGLE_SERVICE_ACCOUNT_FILE)
    candidate_files.append("google_service_account.json")

    for fp in candidate_files:
        if fp and os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)

    raise FileNotFoundError(
        "找不到 Google 憑證。請在 Streamlit secrets 設定 gcp_service_account 或 GOOGLE_SERVICE_ACCOUNT，"
        "或提供 GOOGLE_SERVICE_ACCOUNT_JSON，或放置 google_service_account.json。"
    )


def build_gsheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    service_account_info = get_service_account_info()
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return gspread.authorize(creds)


def load_worksheet(sheet_name):
    client = build_gsheet_client()
    sh = client.open_by_key(GOOGLE_SHEET_ID)
    ws = sh.worksheet(sheet_name)

    values = ws.get_all_values()
    if not values:
        raise Exception(f"工作表 {sheet_name} 沒有資料")

    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    df["__sheet_row__"] = range(2, len(df) + 2)
    return ws, df


def ensure_columns_in_sheet(ws):
    headers = ws.row_values(1)
    required = [
        "簡訊實際服務時間",
        "客人備註",
        "客服備註",
        "訂單編號",
        "結果",
        "原因",
        "沒班表日期",
        "餘額不足未送",
        "確認信",
        "日曆改色結果",
        "日曆改色原因",
        "日曆原色",
        "日曆新色",
        "狀態",
        "服務人員",
        "服務狀態",
        "車馬費",
    ]

    changed = False
    for col in required:
        if col not in headers:
            headers.append(col)
            changed = True

    if changed:
        ws.resize(rows=max(ws.row_count, 1), cols=len(headers))
        ws.update("A1", [headers])

    return headers


def set_customer_notice_clip_style(ws, headers=None, row_numbers=None):
    """
    Google Sheet 顯示規則：
    客服備註內容完整保留，但儲存格視覺上使用「自動裁剪 / CLIP」，
    避免長備註自動換行把列高撐高。
    """
    try:
        headers = headers or ws.row_values(1)
        if "客服備註" not in headers:
            return

        col_index = headers.index("客服備註")  # 0-based
        sheet_id = ws.id

        service_account_info = get_service_account_info()
        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        service = build("sheets", "v4", credentials=creds)

        requests_body = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": col_index,
                        "endColumnIndex": col_index + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "wrapStrategy": "CLIP"
                        }
                    },
                    "fields": "userEnteredFormat.wrapStrategy",
                }
            }
        ]

        # 只固定本次有寫入的資料列，避免長備註撐高列高。
        # row_numbers 是 Google Sheet 的 1-based row number；API 是 0-based index。
        if row_numbers:
            for row_num in sorted(set(int(x) for x in row_numbers if int(x) > 1)):
                requests_body.append(
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": row_num - 1,
                                "endIndex": row_num,
                            },
                            "properties": {
                                "pixelSize": 21
                            },
                            "fields": "pixelSize",
                        }
                    }
                )

        service.spreadsheets().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={"requests": requests_body},
        ).execute()

    except Exception as e:
        print(f"設定客服備註欄位自動裁剪失敗: {e}")


def update_sheet_rows(ws, row_results):
    headers = ensure_columns_in_sheet(ws)
    header_index = {h: i + 1 for i, h in enumerate(headers)}
    updates = []

    for row_num, info in row_results.items():
        xyz = finalize_xyz(
            {
                "服務人員": info.get("服務人員", ""),
                "服務狀態": info.get("服務狀態", ""),
                "車馬費": info.get("車馬費", ""),
            },
            fallback_fare=info.get("車馬費", "0"),
        )
        info["服務人員"] = xyz["服務人員"]
        info["服務狀態"] = xyz["服務狀態"]
        info["車馬費"] = xyz["車馬費"]

        for key, value in info.items():
            if key not in header_index:
                continue

            # I欄「狀態」只允許在成功完成流程時寫入「已安排」。
            # 其他空白或非已安排值都不覆蓋原本的「未安排」。
            if key == "狀態" and str(value).strip() != "已安排":
                continue

            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_num, header_index[key]),
                "values": [[("" if value is None else str(value))]],
            })

    if updates:
        ws.batch_update(updates)
        set_customer_notice_clip_style(ws, headers=headers, row_numbers=row_results.keys())


# =========================
# 後台 API
# =========================
def login(session, email, password):
    resp = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return False

    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if not token_input:
        return False

    token = token_input.get("value", "").strip()
    if not token:
        return False

    resp = session.post(
        LOGIN_URL,
        data={"_token": token, "email": email, "password": password},
        headers=HEADERS,
        allow_redirects=True,
    )
    return resp.status_code == 200 and "login" not in resp.url.lower()


def get_csrf_token(session):
    resp = session.get(BOOKING_URL, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        raise Exception(f"取得儲值金訂單頁失敗: {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if not token_input:
        raise Exception("無法從儲值金訂單頁提取 _token")

    token = token_input.get("value", "").strip()
    if not token:
        raise Exception("_token 為空")

    return token


def get_member(session, phone, token, clean_type_id):
    resp = session.post(
        GET_MEMBER_URL,
        data={"phone": phone, "_token": token, "clean_type_id": clean_type_id},
        headers=HEADERS,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return None

    try:
        result = resp.json()
    except Exception:
        return None

    return result if isinstance(result, dict) and result.get("return_code") == "0000" and result.get("member") else None


def pick_best_address_info(member_payload, target_address):
    """
    強制以真正下拉地址為主；沒有 addressId 視為沒選到下拉地址
    """
    member = member_payload.get("member", {}) if isinstance(member_payload, dict) else {}
    member_address_list = member.get("memberAddressList", []) if isinstance(member, dict) else []

    target_norm = normalize_addr_for_match(target_address)

    for item in member_address_list:
        item_addr = str(item.get("address", "")).strip()
        if normalize_addr_for_match(item_addr) == target_norm:
            return {
                "addressId": str(item.get("id", "")).strip(),
                "country_id": item.get("countryId", ""),
                "area_id": item.get("areaId", ""),
                "address": item_addr,
                "lat": item.get("lat", ""),
                "lng": item.get("lng", ""),
                "company_id": item.get("companyId", 1),
                "purchase": item.get("purchase", {}) if isinstance(item.get("purchase"), dict) else {},
            }

    return {}


def geocode_address(address):
    if not GOOGLE_MAPS_API_KEY:
        return None, None

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": address,
            "language": "zh-TW",
            "key": GOOGLE_MAPS_API_KEY,
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None, None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None, None

        location = results[0].get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            return None, None

        return str(lat), str(lng)
    except Exception:
        return None, None


def check_contain(session, member_id, address, lat, lng, token, clean_type_id):
    resp = session.post(
        CHECK_CONTAIN_URL,
        data={
            "memberId": member_id,
            "cleanTypeId": clean_type_id,
            "address": address,
            "lat": lat or "",
            "lng": lng or "",
            "_token": token,
        },
        headers=HEADERS,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return None

    try:
        return resp.json()
    except Exception:
        return None


def calculate_hour(session, order_data, token):
    data = order_data.copy()
    data["_token"] = token

    resp = session.post(CALCULATE_HOUR_URL, data=data, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return None

    try:
        return resp.json()
    except Exception:
        return None


def extract_calc_fields(calc_result, fallback_hours="", fallback_fare="0"):
    """
    calculate_hour 的回傳格式可能是 dict/list/html/string。
    手動流程是先送 hour/price/fare 空值，後台回傳後再填入：
    hour=4, price=4771, fare=200。
    這裡用遞迴 + 字串 regex 雙重解析。
    """
    def regex_find(text, names):
        text = str(text or "")
        for name in names:
            patterns = [
                rf'"{re.escape(name)}"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
                rf"'{re.escape(name)}'\s*:\s*'?([0-9]+(?:\.[0-9]+)?)'?",
                rf'name=["\\\']{re.escape(name)}["\\\'][^>]*value=["\\\']?([0-9]+(?:\.[0-9]+)?)',
                rf'id=["\\\']{re.escape(name)}["\\\'][^>]*value=["\\\']?([0-9]+(?:\.[0-9]+)?)',
                rf'{re.escape(name)}=([0-9]+(?:\.[0-9]+)?)',
            ]
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    return m.group(1)
        return ""

    if isinstance(calc_result, (dict, list)):
        hour = find_nested_value(calc_result, [
            "hour", "clean_hour", "hours", "total_hour", "service_hour"
        ])
        price = find_nested_value(calc_result, [
            "price", "total_price", "service_price", "amount", "total", "money"
        ])
        price_vvip = find_nested_value(calc_result, [
            "price_vvip", "vvip_price", "vip_price"
        ])
        fare = find_nested_value(calc_result, [
            "fare", "car_fare", "traffic_fee", "trafficFee", "carFare", "車馬費"
        ])
    else:
        hour = price = price_vvip = fare = ""

    raw_text = json.dumps(calc_result, ensure_ascii=False) if isinstance(calc_result, (dict, list)) else str(calc_result or "")

    if not hour:
        hour = regex_find(raw_text, ["hour", "clean_hour", "hours", "total_hour", "service_hour"])
    if not price:
        price = regex_find(raw_text, ["price", "total_price", "service_price", "amount", "total", "money"])
    if not price_vvip:
        price_vvip = regex_find(raw_text, ["price_vvip", "vvip_price", "vip_price"])
    if not fare:
        fare = regex_find(raw_text, ["fare", "car_fare", "traffic_fee", "trafficFee", "carFare"])

    return {
        "hour": str(hour or fallback_hours or ""),
        "price": first_nonzero(price, default="0"),
        "price_vvip": str(price_vvip or "0"),
        "fare": first_nonzero(fare, fallback_fare, default="0"),
    }


def get_section_raw(session, order_data, token, date_slot):
    data = order_data.copy()
    data["_token"] = token
    data["date_list[]"] = date_slot

    resp = session.post(GET_SECTION_URL, data=data, headers=HEADERS, allow_redirects=True)
    return resp.text if resp.status_code == 200 else ""


def extract_cleaners_from_section_response(raw_text, date_slot):
    """
    從 get_section 回傳抓指定日期/時段的人員。
    支援 JSON list：
    [{"date":"2026-05-14","section":"14:00-18:00","cleaner":["胡偉勝"]}]
    """
    if not raw_text:
        return []

    date_part, period_part = date_slot.split("_", 1)
    raw = str(raw_text)

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("data") or data.get("result") or data.get("sections") or []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_date = str(item.get("date", "")).strip()
                item_section = str(item.get("section", "")).strip().replace(" ", "")
                if item_date == date_part and item_section == period_part.replace(" ", ""):
                    cleaners = item.get("cleaner") or item.get("cleaners") or []
                    if isinstance(cleaners, list):
                        return [str(x).strip().lstrip("＊*") for x in cleaners if str(x).strip()]
                    if isinstance(cleaners, str) and cleaners.strip():
                        return [x.strip().lstrip("＊*") for x in re.split(r"[,，、/]+", cleaners) if x.strip()]
    except Exception:
        pass

    text = html.unescape(raw)
    try:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    except Exception:
        pass

    compact = re.sub(r"\s+", "", text)
    d = date_part
    p = period_part.replace(" ", "")
    idx = compact.find(d)
    if idx >= 0:
        nearby = compact[idx:idx + 600]
        pidx = nearby.find(p)
        if pidx >= 0:
            nearby = nearby[pidx:pidx + 500]
            m = re.search(r"[（(]([^）)]+)[）)]", nearby)
            if m:
                return [x.strip().lstrip("＊*") for x in re.split(r"[,，、/]+", m.group(1)) if x.strip()]

    return []


def clean_staff_name(name):
    """
    將班表/訂單頁的人名清成純姓名。
    例：
    - 00洪暐智(4) -> 洪暐智
    - X蔡佩玲(1) -> 蔡佩玲
    - ＊黃惟芊 -> 黃惟芊
    - 吳豐閔 X X蔡佩玲 -> 會在 normalize_staff_display 再統一成 吳豐閔 X 蔡佩玲
    """
    text = html.unescape(str(name or "")).strip()
    if not text:
        return ""

    text = text.strip().lstrip("＊*").strip()
    text = re.sub(r"^[Xx×＊*\s]+", "", text).strip()
    text = re.sub(r"^\d+", "", text).strip()
    text = re.sub(r"[（(]\d+[）)]", "", text).strip()
    text = re.sub(r"^[Xx×\s]+", "", text).strip()
    text = re.sub(r"[Xx×\s]+$", "", text).strip()
    return text


def normalize_staff_display(value, limit=None):
    """
    X欄顯示規則：名字和名字中間只保留一個「 X 」。
    不管來源是 list、已經串好的字串、或含有 X姓名，都先拆開、清洗、再重組。
    """
    if value in (None, ""):
        return ""

    if isinstance(value, (list, tuple)):
        raw_parts = []
        for item in value:
            raw_parts.extend(re.split(r"\s*[Xx×]\s*|[,，、/]+", str(item or "")))
    else:
        raw_parts = re.split(r"\s*[Xx×]\s*|[,，、/]+", str(value or ""))

    cleaned = []
    seen = set()
    for part in raw_parts:
        name = clean_staff_name(part)
        if not name or name in seen:
            continue
        cleaned.append(name)
        seen.add(name)
        if limit and len(cleaned) >= int(limit):
            break

    return " X ".join(cleaned)


def format_staff_from_cleaners(cleaners, people=None):
    try:
        limit = int(people) if people not in (None, "") else None
    except Exception:
        limit = None

    staff = normalize_staff_display(cleaners or [], limit=limit)
    return staff if staff else "無人力"


def slot_exists_in_section_response(raw_text, date_slot):
    """
    get_section 回傳可能是 HTML、JSON 包 HTML、escaped HTML。
    這裡不要只做單一 regex，改成多種格式都可比對。
    """
    if not raw_text:
        return False

    date_part, period_part = date_slot.split("_", 1)
    start_part, end_part = period_part.split("-", 1)

    raw = str(raw_text)
    unescaped = html.unescape(raw)

    try:
        soup_text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    except Exception:
        soup_text = unescaped

    candidates = [raw, unescaped, soup_text]

    date_variants = list(dict.fromkeys([
        date_part,
        date_part.replace("-", "/"),
        date_part.replace("-", ""),
    ]))

    period_variants = list(dict.fromkeys([
        period_part,
        period_part.replace(" ", ""),
        f"{start_part} - {end_part}",
        f"{start_part}~{end_part}",
        f"{start_part}～{end_part}",
    ]))

    for text in candidates:
        compact = re.sub(r"\s+", "", text)

        for d in date_variants:
            for p in period_variants:
                dp = re.sub(r"\s+", "", d)
                pp = re.sub(r"\s+", "", p)
                if dp in compact and pp in compact:
                    date_idx = compact.find(dp)
                    period_idx = compact.find(pp)
                    if date_idx >= 0 and period_idx >= 0 and abs(period_idx - date_idx) < 500:
                        return True

        for d in date_variants:
            d_re = re.escape(d)
            s_re = re.escape(start_part)
            e_re = re.escape(end_part)
            patterns = [
                rf"{d_re}.{{0,500}}{s_re}\s*[-~～]\s*{e_re}",
                rf"{d_re}.{{0,500}}{re.escape(period_part)}",
            ]
            for pat in patterns:
                if re.search(pat, text, flags=re.S):
                    return True

    return False


# =========================
# 檸檬人勾班工具函式（v2026-07：與 quick_order.py 保持一致邏輯，供批次流程共用）
# =========================
VALUE_TO_SHIFT_CODE = {
    "6": "全6", "8": "全8",
    "0830-1230": "上4", "0900-1200": "上3", "0900-1100": "上2",
    "1400-1600": "下2", "1400-1700": "下3", "1400-1800": "下4",
    "1900-2100": "晚2",
}
SHIFT_CONFLICT_TABLE = {
    "全6": {"上3", "上4", "上2", "全6", "全8"},
    "全8": {"上3", "上4", "上2", "下2", "下3", "下4", "全6", "全8"},
    "上3": {"上3", "上4", "上2", "全6", "全8"},
    "上4": {"上3", "上4", "上2", "全6", "全8"},
    "上2": {"上3", "上4", "上2", "全6", "全8"},
    "下3": {"下2", "下3", "下4", "全6", "全8"},
    "下4": {"下2", "下3", "下4", "全6", "全8"},
    "下2": {"下2", "下3", "下4", "全6", "全8"},
}
PERIOD_TO_SHIFT_CODE = {
    "09:00-12:00": "上3", "08:30-12:30": "上4", "09:00-11:00": "上2",
    "14:00-16:00": "下2", "14:00-17:00": "下3", "14:00-18:00": "下4",
    "09:00-16:00": "全6", "09:00-18:00": "全8",
}


def _period_to_shift_code(period_s):
    compact = str(period_s or "").replace(" ", "")
    return PERIOD_TO_SHIFT_CODE.get(compact, "")


def _shift_value_to_code(value):
    value = str(value or "").strip()
    return VALUE_TO_SHIFT_CODE.get(value, value)


def _shift_code_to_value(code):
    code = str(code or "").strip()
    for value, mapped in VALUE_TO_SHIFT_CODE.items():
        if mapped == code:
            return value
    return code


def _shift_code_to_group(code):
    code = str(code or "").strip()
    if code in {"全6", "全8"}:
        return "all"
    if code in {"上2", "上3", "上4"}:
        return "1"
    if code in {"下2", "下3", "下4"}:
        return "2"
    if code in {"晚2"}:
        return "3"
    return "1"


def _shift_codes_conflict(existing_code, target_code):
    existing_code = _shift_value_to_code(existing_code)
    target_code = _shift_value_to_code(target_code)
    if not existing_code or not target_code:
        return False
    if existing_code == target_code:
        return False
    if existing_code in {"全6", "全8"} or target_code in {"全6", "全8"}:
        return True
    return target_code in SHIFT_CONFLICT_TABLE.get(existing_code, set())


def _parse_cleaner_shift_page(html_text, date_str=None):
    token_m = re.search(r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']', html_text or "")
    csrf = token_m.group(1) if token_m else ""
    if not csrf:
        meta_m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html_text or "")
        csrf = meta_m.group(1) if meta_m else ""
    checked_fields = []
    checked_codes_on_date = set()
    for m in re.finditer(r'<input\b[^>]*\bchecked\b[^>]*>', html_text or "", re.I):
        tag = m.group(0)
        name_m = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.I)
        value_m = re.search(r'\bvalue=["\']?([^"\'\s>]+)', tag, re.I)
        date_m = re.search(r'\bdate=["\']([^"\']+)["\']', tag, re.I)
        if not name_m or not value_m:
            continue
        name = name_m.group(1)
        value = value_m.group(1)
        checked_fields.append((name, value))
        d = date_m.group(1) if date_m else ""
        if date_str and d == date_str:
            checked_codes_on_date.add(_shift_value_to_code(value))
    return csrf, checked_fields, checked_codes_on_date


def _get_cleaner_shift_form_info(session, base_url, cleaner_id, date_str):
    ym = str(date_str)[:7]
    resp = session.get(f"{base_url}/cleaner1/{cleaner_id}/shift", params={"month": ym}, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return "", [], set(), f"HTTP {resp.status_code}"
    csrf, checked_fields, checked_codes = _parse_cleaner_shift_page(resp.text, date_str)
    return csrf, checked_fields, checked_codes, ""


def _search_lemon_cleaners(session, base_url, target_month=None, min_needed=0):
    entries = []
    seen_ids = set()
    seen_names = set()
    target_month = str(target_month or datetime.today().strftime("%Y-%m"))[:7]
    min_needed = int(min_needed or 0)

    def lemon_sort_key(item):
        m = re.search(r"檸檬人\s*(\d+)", item[1])
        return int(m.group(1)) if m else 9999

    def add_entry(cid, name):
        cid = str(cid or "").strip()
        name = re.sub(r"\s+", "", str(name or "").strip())
        m = re.search(r"檸檬人\d+", name)
        if m:
            name = m.group(0)
        if not cid or cid in seen_ids or "檸檬人" not in name:
            return
        if name in seen_names:
            return
        seen_ids.add(cid)
        seen_names.add(name)
        entries.append((cid, name))

    candidate_ids = []

    def add_candidate(cid):
        cid = str(cid or "").strip()
        if cid.isdigit() and cid not in candidate_ids:
            candidate_ids.append(cid)

    try:
        resp = session.get(f"{base_url}/cleaner1", params={"area_id": "", "keyword": "檸檬"}, headers=HEADERS, allow_redirects=True)
    except Exception:
        resp = None

    if resp is not None and resp.status_code == 200:
        page_html = resp.text or ""
        row_blocks = re.split(r"<tr\b", page_html, flags=re.I)
        for row in row_blocks:
            if "檸檬人" not in row:
                continue
            name_m = re.search(r"檸檬人\d+", row)
            ids = re.findall(r"/cleaner1/(\d+)(?=[/'\"?#])", row, re.I)
            ids += re.findall(r"cleaner[_-]?id[=:'\" ]+(\d+)", row, re.I)
            for cid in ids:
                add_candidate(cid)
                if name_m:
                    add_entry(cid, name_m.group(0))
        for m in re.finditer(r"/cleaner1/(\d+)(?=[/'\"?#])", page_html, re.I):
            cid = m.group(1)
            ctx = page_html[max(0, m.start() - 1000): m.end() + 1000]
            name_m = re.search(r"檸檬人\d+", ctx)
            add_candidate(cid)
            if name_m:
                add_entry(cid, name_m.group(0))

    entries.sort(key=lemon_sort_key)
    if min_needed and len(entries) >= min_needed:
        return entries

    for cid in list(range(1, 501)):
        add_candidate(cid)

    for cid in candidate_ids:
        if str(cid) in seen_ids:
            continue
        try:
            r = session.get(f"{base_url}/cleaner1/{cid}/shift", params={"month": target_month}, headers=HEADERS, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        txt = r.text or ""
        name_m = re.search(r"專員\s*[：:]\s*(?:<[^>]+>\s*)*(檸檬人\d+)", txt)
        if not name_m:
            name_m = re.search(r"<label>\s*(檸檬人\d+)\s*</label>", txt)
        if name_m:
            add_entry(cid, name_m.group(1))
            entries.sort(key=lemon_sort_key)
            if min_needed and len(entries) >= min_needed:
                break

    entries.sort(key=lemon_sort_key)
    return entries


def _set_cleaner_shift_if_available(session, base_url, cleaner_id, cleaner_name, date_str, target_shift_code):
    csrf, checked_fields, checked_codes, err = _get_cleaner_shift_form_info(session, base_url, cleaner_id, date_str)
    if err:
        return {"success": False, "name": cleaner_name, "id": cleaner_id, "reason": err, "checked": sorted(checked_codes)}
    target_shift_code = _shift_value_to_code(target_shift_code)
    conflicts = sorted(c for c in checked_codes if _shift_codes_conflict(c, target_shift_code))
    if conflicts:
        return {"success": False, "name": cleaner_name, "id": cleaner_id, "reason": f"{date_str} 已勾 {'、'.join(conflicts)}，與 {target_shift_code} 衝突", "checked": sorted(checked_codes)}
    if target_shift_code in checked_codes:
        return {"success": False, "name": cleaner_name, "id": cleaner_id, "reason": f"{date_str} {target_shift_code} 已勾班（可能已有其他訂單使用），換下一位", "checked": sorted(checked_codes), "already_checked": True}
    target_name = f"shift_{date_str}_{_shift_code_to_group(target_shift_code)}"
    target_value = _shift_code_to_value(target_shift_code)
    fields = []
    if csrf:
        fields.append(("_token", csrf))
    seen = set()
    for name, value in checked_fields:
        key = (name, value)
        if key in seen:
            continue
        seen.add(key)
        fields.append((name, value))
    if (target_name, target_value) not in seen:
        fields.append((target_name, target_value))
    resp = session.post(f"{base_url}/cleaner1/{cleaner_id}/shift", params={"month": str(date_str)[:7]}, data=fields, headers=HEADERS, allow_redirects=True)
    ok = resp.status_code in (200, 302)
    return {
        "success": ok, "name": cleaner_name, "id": cleaner_id,
        "message": f"{cleaner_name} 已補勾 {date_str} {target_shift_code}" if ok else f"POST 失敗：HTTP {resp.status_code}",
        "checked": sorted(checked_codes), "target": target_shift_code,
    }


def ensure_lemon_cleaner_shifts(session, base_url, service_date, period_s, person_count):
    """
    v2026-07：查無班表時補勾檸檬人排班。與 quick_order.py 的同名函式邏輯一致，
    供批次（Google Sheet）流程共用，確保五個成單功能行為一致。
    呼叫端必須自行決定是否要在「查無班表」時呼叫本函式（由
    allow_auto_lemon_shift 參數控制），本函式本身不做開關判斷。
    """
    target_shift_code = _period_to_shift_code(period_s)
    if not target_shift_code:
        return {"success": False, "message": f"無法判斷服務時段 {period_s} 對應班別", "assigned": [], "skipped": []}
    cleaners = _search_lemon_cleaners(session, base_url, target_month=str(service_date)[:7], min_needed=int(person_count))
    if not cleaners:
        return {"success": False, "message": "找不到檸檬人清單", "assigned": [], "skipped": []}
    need = int(person_count)
    assigned = []
    assigned_ids = []
    skipped = []
    seen_candidate_names = set()
    seen_candidate_ids = set()
    for cleaner_id, cleaner_name in cleaners:
        if str(cleaner_id) in seen_candidate_ids or str(cleaner_name) in seen_candidate_names:
            continue
        seen_candidate_ids.add(str(cleaner_id))
        seen_candidate_names.add(str(cleaner_name))
        if len(assigned) >= need:
            break
        result = _set_cleaner_shift_if_available(session, base_url, cleaner_id, cleaner_name, service_date, target_shift_code)
        if result.get("success"):
            assigned.append(cleaner_name)
            assigned_ids.append(str(cleaner_id))
        else:
            skipped.append(result)
    ok = len(assigned) >= need
    return {
        "success": ok,
        "message": f"已預先補勾檸檬人：{'、'.join(assigned)}" if ok else f"可用檸檬人不足：需要 {need} 位，找到 {len(assigned)} 位",
        "assigned": assigned, "assigned_ids": assigned_ids, "skipped": skipped, "target_shift_code": target_shift_code,
    }


# =========================
# Purchase 頁解析
# =========================
def extract_order_cards_from_purchase_html(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    blocks = []
    current = None

    for line in lines:
        if re.fullmatch(ORDER_NO_REGEX, line):
            if current:
                blocks.append(current)
            current = {"order_no": line, "lines": [line]}
        elif current:
            current["lines"].append(line)

    if current:
        blocks.append(current)

    return blocks


def match_order_from_purchase_page(html, target_date, target_period, phone="", exclude_order_nos=None):
    """
    v2026-07：比對日期＋時段，且若有提供 phone 則同時比對電話，避免只用
    日期+時段配對到別人的訂單（這是造成同一個訂單編號被誤寫進兩列
    Google Sheet「M欄重複」的根因——原本完全沒有比對電話）。
    exclude_order_nos 可排除本次批次已經用掉的訂單編號，進一步降低誤配對機率。
    """
    exclude_order_nos = exclude_order_nos or set()
    target_phone_norm = normalize_phone(phone) if phone else ""
    fallback_candidate = None
    for block in extract_order_cards_from_purchase_html(html):
        order_no_candidate = block.get("order_no")
        if not order_no_candidate or order_no_candidate in exclude_order_nos:
            continue
        joined = "\n".join(block["lines"])
        if target_date not in joined or target_period not in joined:
            continue
        if not target_phone_norm:
            return order_no_candidate
        joined_compact = re.sub(r"[-\s]", "", joined)
        if target_phone_norm in joined_compact:
            return order_no_candidate
        if fallback_candidate is None:
            fallback_candidate = order_no_candidate
    # 找不到電話完全相符的訂單時，退回原本「只比對日期+時段」的第一筆結果，
    # 並由呼叫端的一致性檢查（verify_batch_order_consistency）事後抓出異常。
    return fallback_candidate


def fetch_order_no_by_date_and_period(session, target_date, target_period, phone="", exclude_order_nos=None):
    resp = session.get(PURCHASE_URL, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return None
    return match_order_from_purchase_page(resp.text, target_date, target_period, phone=phone, exclude_order_nos=exclude_order_nos)


def _extract_staff_line(lines):
    joined = "\n".join(lines)
    normalized = normalize_text_for_parse(joined)

    # 支援任意人數的服務人員，例如：
    # 陳靜怡(3)X蔡宗原(5)X鄭蓓婷(1)
    # 余世煒(3)X黃惟芊(2)X檸檬人1(0)
    #
    # 舊寫法只抓到前兩個 group，因此 3 人以上會少顯示。
    # 這裡改成先找「連續 X 串接的人員群組」，並取人數最多的那一組。
    staff_token = r"[\u4e00-\u9fffA-Za-z0-9]+[（(]\d+[）)]"
    staff_group_pattern = rf"{staff_token}(?:[Xx×]{staff_token})+"

    groups = re.findall(staff_group_pattern, normalized)
    if groups:
        best_group = max(
            groups,
            key=lambda value: len(re.findall(staff_token, value)),
        )
        return normalize_staff_display(best_group)

    # 支援只有 1 位服務人員的訂單，例如：檸檬人1(0)
    singles = re.findall(staff_token, normalized)
    if singles:
        return normalize_staff_display(singles[0])

    return "無人力"


def _extract_status_line(lines):
    joined = "\n".join(lines)
    normalized = normalize_text_for_parse(joined)

    for status in KNOWN_SERVICE_STATUS:
        if status in normalized:
            return status

    if "未處理" in normalized:
        return "未處理"
    if "已處理" in normalized:
        return "已處理"

    return "未處理"


def _extract_fare_line(lines):
    joined = "\n".join(lines)
    normalized = normalize_text_for_parse(joined)

    m = re.search(r'車馬費[：:]?(\d+)', normalized)
    if m:
        return m.group(1)

    return "0"


def _extract_service_date_time(lines):
    service_date = ""
    service_time = ""

    for idx, line in enumerate(lines):
        text = line.strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", text):
            service_date = text[:10]

            for j in range(idx + 1, min(idx + 5, len(lines))):
                nxt = lines[j].strip().replace(" ", "")
                if re.match(r"\d{2}:\d{2}-\d{2}:\d{2}", nxt):
                    service_time = nxt
                    break
            break

    return service_date, service_time


def fetch_order_meta_by_order_no(session, order_no):
    resp = session.get(PURCHASE_URL, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return {
            "服務人員": "無人力",
            "服務狀態": "未處理",
            "車馬費": "0",
            "服務日期": "",
            "服務時間": "",
        }

    blocks = extract_order_cards_from_purchase_html(resp.text)
    for block in blocks:
        if block["order_no"] == order_no:
            lines = block.get("lines", [])
            service_date, service_time = _extract_service_date_time(lines)
            staff = _extract_staff_line(lines)
            status = _extract_status_line(lines)
            fare = _extract_fare_line(lines)

            return {
                "服務人員": staff if staff else "無人力",
                "服務狀態": status if status else "未處理",
                "車馬費": fare if fare else "0",
                "服務日期": service_date,
                "服務時間": service_time,
            }

    return {
        "服務人員": "無人力",
        "服務狀態": "未處理",
        "車馬費": "0",
        "服務日期": "",
        "服務時間": "",
    }


def verify_batch_order_consistency(session, df, all_row_results):
    """
    v2026.07.04：雙向比對 Google Sheet 與後台系統訂單是否一致。

    方向一（從 Google Sheet 比對系統）：
        逐列拿寫回的訂單編號回查系統，比對「電話、地址、日期、時段」是否跟
        Google Sheet 這一列的資料相符，抓出：
        1. 同一個訂單編號被寫進超過一列（M欄重複）。
        2. 訂單編號在後台查無資料。
        3. 訂單編號查得到，但電話/地址/日期/時段跟這一列對不上（代表訂單編號
           很可能誤配對到別人的訂單，這一列實際上沒有真的成單）。

    方向二（從系統的日期區間比對 Google Sheet）：
        以這批次涉及的每支電話，查詢系統該電話底下的實際訂單，只看落在這批次
        涉及日期範圍內的訂單，確認每一筆系統訂單都能對應回 Google Sheet 某一列
        寫下的訂單編號，抓出「系統其實已經成單，但 Google Sheet 沒有正確記錄
        （M欄空白、或寫的是別的訂單編號）」的情況——這是方向一（只看 Sheet 已
        填寫的編號）照不到的死角。

    回傳 list of dict：[{row_num, order_no, issue}, ...]（方向二查到的問題
    row_num 為 None，因為它不是從特定一列出發）。沒有問題則回傳空 list。
    查詢過程中任何單筆錯誤都不中斷整體檢查，只會跳過該筆。
    """
    problems = []
    row_lookup = {}
    for _, row in df.iterrows():
        try:
            row_lookup[int(row["__sheet_row__"])] = row
        except Exception:
            continue

    seen_order_nos = {}
    # 方向二用：記錄每支電話在 Google Sheet 上「認定」的 (日期, 訂單編號) 組合，
    # 以及這批次涉及的日期範圍，供之後反查系統訂單時比對。
    phone_sheet_records = defaultdict(list)

    # ---------- 方向一：從 Google Sheet 出發，回查系統 ----------
    for row_num, result in all_row_results.items():
        row_num_int = int(row_num)
        row = row_lookup.get(row_num_int)
        order_no = str(result.get("訂單編號", "") or "").strip()

        # 不管這一列有沒有訂單編號，只要抓得到電話/日期，都先記錄下來供方向二比對
        if row is not None:
            try:
                _phone_for_dir2 = normalize_phone(row.get("電話", ""))
                _date_for_dir2 = get_date_str(row["日期"])
                if _phone_for_dir2:
                    phone_sheet_records[_phone_for_dir2].append({
                        "row_num": row_num_int, "date": _date_for_dir2, "order_no": order_no,
                    })
            except Exception:
                pass

        if not order_no:
            continue

        # 同一個訂單編號被寫進超過一列 → 直接標記重複（這是「M欄重複」最直接的證據）
        if order_no in seen_order_nos:
            problems.append({
                "row_num": row_num_int,
                "order_no": order_no,
                "issue": f"訂單編號 {order_no} 與第 {seen_order_nos[order_no]} 列重複，這兩列很可能只有一列真的成單，另一列請重新確認。",
            })
        else:
            seen_order_nos[order_no] = row_num_int

        if row is None:
            continue

        try:
            phone = normalize_phone(row.get("電話", ""))
            date_s = get_date_str(row["日期"])
            period_s = normalize_sheet_period(row["開始時間"], row["結束時間"])
            display_period = display_period_text(period_s.split("-")[0], period_s.split("-")[1])
            address = str(row.get("地址", "")).strip()
        except Exception:
            continue

        try:
            _params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
            _params["orderNo"] = order_no
            resp = session.get(PURCHASE_URL, params=_params, headers=HEADERS, allow_redirects=True)
        except Exception:
            continue
        if resp.status_code != 200:
            continue

        actual_block = None
        for block in extract_order_cards_from_purchase_html(resp.text):
            if block.get("order_no") == order_no:
                actual_block = block
                break

        if not actual_block:
            problems.append({
                "row_num": row_num_int,
                "order_no": order_no,
                "issue": f"訂單編號 {order_no} 在後台查無資料，第 {row_num_int} 列很可能其實沒有真的成單。",
            })
            continue

        joined = "\n".join(actual_block.get("lines", []))
        joined_compact = re.sub(r"[-\s]", "", joined)
        phone_match = (not phone) or (phone in joined_compact)
        date_match = date_s in joined
        period_match = (
            display_period.replace(" ", "") in joined.replace(" ", "")
            or period_s.replace(" ", "") in joined.replace(" ", "")
        )
        address_match = True
        if address:
            addr_norm = normalize_addr_for_match(address)
            joined_addr_norm = normalize_addr_for_match(joined)
            # 地址核心片段（去掉樓層等細節差異的風險）只要有出現在訂單內容即可算相符，
            # 避免因為門牌格式些微差異（例如全形/半形號樓字）誤判成不相符。
            core = addr_norm[:10] if len(addr_norm) >= 10 else addr_norm
            address_match = bool(core) and core in joined_addr_norm

        if not (phone_match and date_match and period_match and address_match):
            problems.append({
                "row_num": row_num_int,
                "order_no": order_no,
                "issue": (
                    f"訂單 {order_no} 實際內容跟 Google Sheet 第 {row_num_int} 列不符"
                    f"（電話符合：{phone_match}，地址符合：{address_match}，"
                    f"日期符合：{date_match}，時段符合：{period_match}），"
                    f"很可能是訂單編號誤配對到別人的訂單，此列可能其實沒有真的成單，請人工確認。"
                ),
            })

    # ---------- 方向二：從系統的日期區間出發，反查 Google Sheet ----------
    for phone, sheet_records in phone_sheet_records.items():
        relevant_dates = {r["date"] for r in sheet_records if r.get("date")}
        sheet_order_nos_for_phone = {r["order_no"] for r in sheet_records if r.get("order_no")}
        if not relevant_dates:
            continue
        try:
            _params = dict(PURCHASE_FILTER_PARAMS_TEMPLATE)
            _params["phone"] = phone
            resp = session.get(PURCHASE_URL, params=_params, headers=HEADERS, allow_redirects=True)
        except Exception:
            continue
        if resp.status_code != 200:
            continue

        for block in extract_order_cards_from_purchase_html(resp.text):
            sys_order_no = block.get("order_no")
            if not sys_order_no:
                continue
            joined = "\n".join(block.get("lines", []))
            matched_date = ""
            for _d in relevant_dates:
                if _d in joined:
                    matched_date = _d
                    break
            if not matched_date:
                continue  # 這筆系統訂單不在這批次涉及的日期範圍內，略過

            if sys_order_no not in sheet_order_nos_for_phone:
                problems.append({
                    "row_num": None,
                    "order_no": sys_order_no,
                    "issue": (
                        f"系統查到電話 {phone} 在 {matched_date} 有一筆訂單 {sys_order_no}，"
                        f"但 Google Sheet 這批次處理的列裡找不到寫著這個訂單編號的紀錄"
                        f"（可能是某一列的訂單編號欄位空白或寫錯），請確認是否有列遺漏記錄。"
                    ),
                })

    return problems


def send_confirmation_mail(session, order_no):
    url = MAIL_SUCCESS_URL.format(order_no=order_no)
    resp = session.get(url, headers=MAIL_HEADERS, allow_redirects=True)

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"

    try:
        return True, str(resp.json())
    except Exception:
        return True, resp.text[:200]


# =========================
# Google Calendar
# =========================
def build_gcal_service():
    if not ENABLE_GCAL_COLOR_SYNC:
        return None

    scopes = ["https://www.googleapis.com/auth/calendar"]
    service_account_info = get_service_account_info()
    credentials = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return build("calendar", "v3", credentials=credentials)


def parse_event_time(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(dt_str, "%Y-%m-%d")
        except Exception:
            return None


def color_name_from_id(color_id):
    mapping = {
        "1": "薰衣草紫",
        "2": "鼠尾草綠",
        "3": "葡萄紫",
        "4": "火鶴紅",
        "5": "香蕉黃",
        "6": "橘子橙",
        "7": "孔雀藍",
        "8": "石墨灰",
        "9": "藍莓藍",
        "10": "羅勒綠",
        "11": "番茄紅",
    }
    return mapping.get(str(color_id), f"未知({color_id})")


def find_matching_calendar_event(service, calendar_id, address, target_date, start_time_str, end_time_str):
    target_date_obj = parse_date_value(target_date)
    sh, sm, eh, em = parse_time_slot(start_time_str, end_time_str)

    tz = timezone(timedelta(hours=8))
    day_start = datetime(target_date_obj.year, target_date_obj.month, target_date_obj.day, 0, 0, 0, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    events = service.events().list(
        calendarId=calendar_id,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    target_addr = normalize_addr_for_match(address)

    for event in events:
        start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        end_raw = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        start_dt = parse_event_time(start_raw)
        end_dt = parse_event_time(end_raw)
        if not start_dt or not end_dt:
            continue

        location = event.get("location", "") or ""
        description = event.get("description", "") or ""
        summary = event.get("summary", "") or ""
        text_blob = normalize_addr_for_match(location + " " + description + " " + summary)

        if (
            start_dt.date() == target_date_obj.date()
            and (start_dt.hour, start_dt.minute) == (sh, sm)
            and (end_dt.hour, end_dt.minute) == (eh, em)
            and target_addr
            and target_addr in text_blob
        ):
            return event

    return None


def sync_calendar_color_for_row(service, calendar_id, address, date_value, start_time_str, end_time_str):
    if not ENABLE_GCAL_COLOR_SYNC or service is None:
        return {
            "日曆改色結果": "未執行",
            "日曆改色原因": "未啟用日曆改色",
            "日曆原色": "",
            "日曆新色": "",
        }

    try:
        event = find_matching_calendar_event(service, calendar_id, address, date_value, start_time_str, end_time_str)
    except HttpError as e:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": f"Calendar API 錯誤: {e}",
            "日曆原色": "",
            "日曆新色": "",
        }
    except Exception as e:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": f"Calendar 例外: {e}",
            "日曆原色": "",
            "日曆新色": "",
        }

    if not event:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": "找不到對應日曆事件",
            "日曆原色": "",
            "日曆新色": "",
        }

    event_id = event.get("id")
    old_color = str(event.get("colorId", ""))
    old_color_name = color_name_from_id(old_color)

    if old_color != COLOR_PURPLE:
        return {
            "日曆改色結果": "未改",
            "日曆改色原因": f"需求有異動（原色：{old_color_name}）",
            "日曆原色": old_color_name,
            "日曆新色": old_color_name,
        }

    try:
        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"colorId": COLOR_YELLOW},
        ).execute()
    except HttpError as e:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": f"改色 API 錯誤: {e}",
            "日曆原色": old_color_name,
            "日曆新色": old_color_name,
        }
    except Exception as e:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": f"改色例外: {e}",
            "日曆原色": old_color_name,
            "日曆新色": old_color_name,
        }

    return {
        "日曆改色結果": "成功",
        "日曆改色原因": "葡萄紫 → 香蕉黃",
        "日曆原色": old_color_name,
        "日曆新色": color_name_from_id(COLOR_YELLOW),
    }


# =========================
# 各階段
# =========================
def prepare_base_order_data(row, member_payload, address_info, clean_type_id, people, hours, system_period, note_info):
    member = member_payload.get("member", {}) if isinstance(member_payload, dict) else {}
    last_purchase = member_payload.get("lastPurchase", {}) if isinstance(member_payload, dict) else {}
    old_purchase = address_info.get("purchase", {}) if isinstance(address_info, dict) else {}

    def pick(key, default=""):
        if old_purchase.get(key) not in (None, ""):
            return old_purchase.get(key)
        if last_purchase.get(key) not in (None, ""):
            return last_purchase.get(key)
        return default

    def pick_address_notice(default=""):
        # 客服備註必須以「下拉地址對應的前次訂單客服備註」為準。
        # 不使用 lastPurchase.notice，避免抓到會員其他地址或最後一筆訂單的備註。
        if address_info.get("notice") not in (None, ""):
            return address_info.get("notice")
        if old_purchase.get("notice") not in (None, ""):
            return old_purchase.get("notice")
        if old_purchase.get("service_notice") not in (None, ""):
            return old_purchase.get("service_notice")
        return default

    base_memo = ""
    if note_info["need_note"]:
        base_memo = note_info["customer_time_note"] if not base_memo else f"{base_memo}；{note_info['customer_time_note']}"

    return {
        "clean_type_id": clean_type_id,
        "phone": normalize_phone(row["電話"]),
        "name": str(member.get("name") or row["姓名"]).strip(),
        "email": str(member.get("email") or "").strip(),
        "tel": str(member.get("tel") or normalize_phone(row["電話"])),
        "line": str(member.get("line") or ""),
        "fbName": str(member.get("fb_name") or ""),
        "fb": str(member.get("fb") or ""),
        "memoProcess": str(member.get("memo_process") or ""),
        "memoFinance": str(member.get("memo_finance") or ""),
        "addressId": str(address_info.get("addressId") or ""),
        "country_id": str(address_info.get("country_id") or pick("country_id", "12")),
        "address": str(row["地址"]).strip(),
        "ping": str(pick("ping", "4")),
        "room": str(pick("room", "0")),
        "bathroom": str(pick("bathroom", "0")),
        "balcony": str(pick("balcony", "0")),
        "livingroom": str(pick("livingroom", "0")),
        "kitchen": str(pick("kitchen", "0")),
        "window": str(pick("window", "")),
        "shutter": str(pick("shutter", "")),
        "clothes": str(pick("clothes", "0")),
        "dyson": str(pick("dyson", "0")),
        "refrigerator": str(pick("refrigerator", "0")),
        "disinfection": str(pick("disinfection", "0")),
        "go_abord": str(pick("go_abord", "0")),
        "home_move": str(pick("home_move", "0")),
        "storage": str(pick("storage", "0")),
        "cabinet": str(pick("cabinet", "0")),
        "quintuple": str(pick("quintuple", "0")),
        "hour": str(int(float(hours))),
        "price": "0",
        "price_vvip": "0",
        "person": str(int(people)),
        "date_s": "",
        "period_s": system_period,
        "period": note_info["sms_time"] if note_info["need_note"] else "",
        "cycle": "1",
        "fare": str(address_info.get("fare") or pick("fare", "0") or "0"),
        "memo": base_memo,
        "notice": str(pick_address_notice("")),
        "discount_code": "",
        "payway": "4",
        "is_backend": "477",
        "member_id": str(member.get("member_id") or ""),
        "company_id": str(address_info.get("company_id") or pick("company_id", "1")),
        "area_id": str(address_info.get("area_id") or pick("area_id", "25")),
        "lat": str(address_info.get("lat") or pick("lat", "")),
        "lng": str(address_info.get("lng") or pick("lng", "")),
    }


def filter_dates_by_balance(date_slots, date_prices, stored_value):
    # 只用服務費 price 判斷，車馬費不算在儲值金
    selected_slots, selected_prices, total = [], [], 0
    for slot, price in zip(date_slots, date_prices):
        if total + price <= stored_value:
            selected_slots.append(slot)
            selected_prices.append(price)
            total += price
    return selected_slots, selected_prices, total


def stage_send_confirmation(order_no, session):
    if not order_no:
        return {"確認信": ""}
    try:
        ok, mail_msg = send_confirmation_mail(session, order_no)
        return {"確認信": "已發送" if ok else f"發送失敗: {mail_msg}"}
    except Exception as e:
        return {"確認信": f"發送失敗: {e}"}


def stage_calendar_color(row, gcal_service, region):
    calendar_id = GOOGLE_CALENDAR_MAP.get(region)
    if not calendar_id:
        return {
            "日曆改色結果": "未執行",
            "日曆改色原因": f"找不到區域 {region} 的日曆設定",
            "日曆原色": "",
            "日曆新色": "",
        }

    try:
        return sync_calendar_color_for_row(
            gcal_service,
            calendar_id,
            str(row["地址"]).strip(),
            row["日期"],
            str(row["開始時間"]).strip(),
            str(row["結束時間"]).strip(),
        )
    except Exception as e:
        return {
            "日曆改色結果": "失敗",
            "日曆改色原因": str(e),
            "日曆原色": "",
            "日曆新色": "",
        }


def stage_update_status(order_no, confirm_info, calendar_info, row_result=None):
    confirm_ok = str(confirm_info.get("確認信", "")).strip() == "已發送"
    calendar_ok = str(calendar_info.get("日曆改色結果", "")).strip() == "成功"

    row_result = row_result or {}
    staff_ok = str(row_result.get("服務人員", "")).strip() not in ("", "無人力")
    service_status_ok = str(row_result.get("服務狀態", "")).strip() != ""
    fare_ok = str(row_result.get("車馬費", "")).strip() != ""

    if order_no and confirm_ok and calendar_ok and staff_ok and service_status_ok and fare_ok:
        return {"狀態": "已安排"}

    return {}


def has_action(selected_actions, action_name):
    return True if not selected_actions else action_name in selected_actions


def process_existing_order_only(row, gcal_service, region, session, selected_actions=None):
    order_no = str(row.get("訂單編號", "")).strip()

    if not order_no:
        return build_row_result(
            result="失敗",
            reason="無訂單編號",
            status_value="",
            staff="無人力",
            service_status="未處理",
            fare="0",
        )

    meta = fetch_order_meta_by_order_no(session, order_no)

    result = build_row_result(
        order_no=order_no,
        result="跳過",
        reason="",
        status_value="",
        staff=meta.get("服務人員", "無人力"),
        service_status=meta.get("服務狀態", "未處理"),
        fare=meta.get("車馬費", "0"),
    )

    did_anything = False
    confirm_info = {}
    calendar_info = {}

    if has_action(selected_actions, "寄確認信"):
        confirm_info = stage_send_confirmation(order_no, session)
        result.update(confirm_info)
        did_anything = True

    if has_action(selected_actions, "改 Google 日曆"):
        calendar_info = stage_calendar_color(row, gcal_service, region)
        result.update(calendar_info)
        did_anything = True

    result.update(stage_update_status(order_no, confirm_info, calendar_info, result))

    if did_anything:
        result["結果"] = "成功"

    return result


def process_one_group(session, rows_with_idx, token, gcal_service, region, backend_user_id=None, selected_actions=None, allow_auto_lemon_shift=False, used_order_nos=None):
    _, row0 = rows_with_idx[0]

    purchase_item = str(row0["購買項目"]).strip()
    clean_type_id = CLEAN_TYPE_MAP.get(purchase_item)
    if not clean_type_id:
        raise Exception(f"未知購買項目: {purchase_item}")

    mapped = map_to_system_slot(row0["開始時間"], row0["結束時間"], row0["服務人時"])
    system_period = mapped["system_slot"]
    system_display_period = display_period_text(system_period.split("-")[0], system_period.split("-")[1])

    people, hours = parse_service_human_hour(row0["服務人時"], row0["開始時間"], row0["結束時間"])
    if hours is None:
        raise Exception("無法判斷服務時數")

    print("[DEBUG] parsed person/hour =", {
        "服務人時": str(row0["服務人時"]),
        "sheet_time": normalize_period_text(row0["開始時間"], row0["結束時間"]),
        "person": people,
        "hour": hours,
    })
    try:
        if st is not None:
            st.write("👥 parsed person/hour =", {
                "服務人時": str(row0["服務人時"]),
                "sheet_time": normalize_period_text(row0["開始時間"], row0["結束時間"]),
                "person": people,
                "hour": hours,
            })
    except Exception:
        pass

    phone = normalize_phone(row0["電話"])
    member_payload = get_member(session, phone, token, clean_type_id)
    if not member_payload:
        raise Exception(f"會員不存在: {phone}")

    member = member_payload.get("member", {})
    stored_value = int(float(member_payload.get("storedValue", 0) or 0))

    target_address = str(row0["地址"]).strip().split(",")[0]
    best_addr = pick_best_address_info(member_payload, target_address)
    if not best_addr:
        raise Exception("找不到對應地址資料")
    if not str(best_addr.get("addressId", "")).strip():
        raise Exception(f"地址存在但未選到下拉地址，缺少 addressId：{target_address}")

    selected_address = str(best_addr.get("address") or target_address).strip()

    geo_lat, geo_lng = geocode_address(selected_address)
    if geo_lat and geo_lng:
        best_addr["lat"] = geo_lat
        best_addr["lng"] = geo_lng

    addr_check = check_contain(
        session,
        member.get("member_id", ""),
        selected_address,
        best_addr.get("lat", ""),
        best_addr.get("lng", ""),
        token,
        clean_type_id,
    )
    if not addr_check:
        raise Exception(f"查詢地址/地區失敗：{selected_address}")

    # 確認是否真的有模擬按下「查詢地址」
    print("[DEBUG] check_contain raw =", addr_check)
    try:
        if st is not None:
            st.write("check_contain raw =", addr_check)
    except Exception:
        pass

    area_info = addr_check.get("area") if isinstance(addr_check.get("area"), dict) else {}
    purchase_info = addr_check.get("purchase") if isinstance(addr_check.get("purchase"), dict) else {}

    if area_info:
        best_addr["area_id"] = area_info.get("area_id", best_addr.get("area_id"))
        best_addr["company_id"] = area_info.get("company_id", best_addr.get("company_id"))
        best_addr["country_id"] = area_info.get("country_id", best_addr.get("country_id"))

    # 注意：check_contain 回傳的 purchase 通常是付款/發票資訊，
    # 不是「下拉地址前一次訂單」的客服備註來源。
    # 所以不能覆蓋 best_addr["purchase"]，否則會把下拉地址 purchase.notice 洗掉。

    # 模擬後台「查詢地址」後的資料補齊：
    # 車馬費可能在 purchase、area 或巢狀欄位中，需全部掃描。
    fare_from_check = first_nonzero(
        purchase_info.get("fare") if purchase_info else "",
        purchase_info.get("car_fare") if purchase_info else "",
        purchase_info.get("traffic_fee") if purchase_info else "",
        area_info.get("fare") if area_info else "",
        area_info.get("car_fare") if area_info else "",
        area_info.get("traffic_fee") if area_info else "",
        find_nested_value(addr_check, ["fare", "car_fare", "traffic_fee", "trafficFee", "車馬費"]),
        best_addr.get("fare", ""),
        default="0",
    )
    best_addr["fare"] = fare_from_check

    # 客服備註來源修正：
    # 後台在選定會員地址 / 查詢地區後，應帶出「該地址前一次訂單」的預設備註。
    # 這裡只接受 check_contain 的 purchase / 該地址 address_info 回傳值，
    # 不使用 area_info.notice，也不使用 member_payload.lastPurchase.notice，
    # 避免抓到區域備註、會員其他地址或最後一筆訂單的備註。
    dropdown_purchase = best_addr.get("purchase", {}) if isinstance(best_addr.get("purchase"), dict) else {}
    notice_from_dropdown_purchase = (
        dropdown_purchase.get("notice")
        or dropdown_purchase.get("service_notice")
        or find_nested_value(dropdown_purchase, ["notice", "service_notice", "memo_notice", "customer_service_notice"])
        or ""
    )
    notice_from_check = (
        notice_from_dropdown_purchase
        or best_addr.get("notice", "")
        or (purchase_info.get("notice") if purchase_info else "")
        or (purchase_info.get("service_notice") if purchase_info else "")
        or find_nested_value(purchase_info, ["notice", "service_notice", "memo_notice", "customer_service_notice"])
        or ""
    )
    best_addr["notice"] = notice_from_check

    base_data = prepare_base_order_data(
        row0,
        member_payload,
        best_addr,
        clean_type_id,
        people,
        hours,
        system_period,
        mapped,
    )

    # 強制套用查詢地址後取得的區域/車馬費資料
    base_data["fare"] = first_nonzero(best_addr.get("fare"), base_data.get("fare"), default="0")
    base_data["notice"] = str(best_addr.get("notice") or base_data.get("notice") or "")
    base_data["area_id"] = str(best_addr.get("area_id") or base_data.get("area_id") or "")
    base_data["company_id"] = str(best_addr.get("company_id") or base_data.get("company_id") or "")
    base_data["country_id"] = str(best_addr.get("country_id") or base_data.get("country_id") or "")
    base_data["addressId"] = str(best_addr.get("addressId") or base_data.get("addressId") or "")
    base_data["lat"] = str(best_addr.get("lat") or base_data.get("lat") or "")
    base_data["lng"] = str(best_addr.get("lng") or base_data.get("lng") or "")

    print("[DEBUG] address check result =", {
        "addressId": base_data.get("addressId"),
        "area_id": base_data.get("area_id"),
        "company_id": base_data.get("company_id"),
        "fare": base_data.get("fare"),
        "lat": base_data.get("lat"),
        "lng": base_data.get("lng"),
    })

    def build_time_fields():
        sms_time = base_data.get("period", "")
        customer_note = base_data.get("memo", "")
        if mapped["need_note"]:
            sms_time = mapped["original_slot"]
            customer_note = f"服務時間：{mapped['original_slot']}"
        return sms_time, customer_note

    def build_priced_payload_for_date(date_s):
        calc_data = base_data.copy()

        # 重要：完全模擬手動「計算時數」流程。
        # 手動 request 會送 date_s/hour/price/price_vvip/fare 空值，
        # 讓後台自行計算 hour/price/fare；若先帶 0，後台可能不會重算。
        # 查詢班表/計算時數前，先把人數與時數改成 Google Sheet/A欄規則後的值。
        # 不採用後台自動推回來的 hour 來決定班表。
        calc_data["date_s"] = date_s
        calc_data["hour"] = str(base_data.get("hour") or "")
        calc_data["person"] = str(base_data.get("person") or "")
        calc_data["price"] = ""
        calc_data["price_vvip"] = ""
        calc_data["fare"] = ""

        calc_result = calculate_hour(session, calc_data, token)
        if not calc_result:
            raise Exception(f"計算時數失敗：{date_s}")

        print("[DEBUG] calculate_hour raw =", calc_result)
        try:
            if st is not None:
                st.write("🟠 calculate_hour raw =", calc_result)
        except Exception:
            pass

        calc_fields = extract_calc_fields(
            calc_result,
            fallback_hours=base_data.get("hour", ""),
            fallback_fare=best_addr.get("fare", "0"),
        )

        payload = base_data.copy()
        payload["date_s"] = date_s
        payload["hour"] = str(base_data.get("hour") or calc_fields.get("hour") or "")
        payload["person"] = str(base_data.get("person") or payload.get("person") or "")
        payload["price"] = str(calc_fields.get("price") or "0")
        payload["price_vvip"] = str(calc_fields.get("price_vvip") or "0")

        print("[DEBUG] calc_fields =", calc_fields)
        try:
            if st is not None:
                st.write("🟣 calc_fields =", calc_fields)
        except Exception:
            pass
        payload["fare"] = first_nonzero(calc_fields.get("fare"), best_addr.get("fare"), base_data.get("fare"), default="0")

        if str(payload.get("price", "")).strip() in ("", "0", "0.0"):
            raise Exception(f"計算時數後 price 仍為 0，請貼 🟠 calculate_hour raw 與 🟣 calc_fields：{date_s}")

        payload["notice"] = str(base_data.get("notice") or best_addr.get("notice") or "")
        payload["area_id"] = str(base_data.get("area_id") or best_addr.get("area_id") or "")
        payload["company_id"] = str(base_data.get("company_id") or best_addr.get("company_id") or "")
        payload["addressId"] = str(base_data.get("addressId") or best_addr.get("addressId") or "")
        return payload

    row_details = []
    for row_num, row in rows_with_idx:
        date_s = get_date_str(row["日期"])
        priced_payload = build_priced_payload_for_date(date_s)

        row_details.append({
            "row_num": row_num,
            "date": date_s,
            "slot": f"{date_s}_{system_period}",
            "price": int(float(priced_payload.get("price") or 0)),  # 只拿服務費比對儲值金
            "display_period": system_display_period,
            "row": row,
            "payload": priced_payload,
        })

        print("[DEBUG] row slot =", {
            "row_num": row_num,
            "sheet_time": normalize_period_text(row["開始時間"], row["結束時間"]),
            "system_period": system_period,
            "slot": f"{date_s}_{system_period}",
            "price": priced_payload.get("price"),
            "fare": priced_payload.get("fare"),
        })
        try:
            if st is not None:
                st.write("🧭 row slot =", {
                    "row_num": row_num,
                    "sheet_time": normalize_period_text(row["開始時間"], row["結束時間"]),
                    "system_period": system_period,
                    "slot": f"{date_s}_{system_period}",
                    "price": priced_payload.get("price"),
                    "fare": priced_payload.get("fare"),
                })
        except Exception:
            pass

    need_create_order = has_action(selected_actions, "建單")
    row_results = {}

    if not need_create_order:
        for detail in row_details:
            existing_order_no = str(detail["row"].get("訂單編號", "")).strip()
            sms_time, customer_note = build_time_fields()
            service_notice = str(detail["payload"].get("notice") or "")

            meta = fetch_order_meta_by_order_no(session, existing_order_no) if existing_order_no else {
                "服務人員": "無人力",
                "服務狀態": "未處理",
                "車馬費": "0",
            }

            result = build_row_result(
                order_no=existing_order_no,
                result="成功" if existing_order_no else "失敗",
                reason="" if existing_order_no else "無訂單編號，無法寄信或改日曆",
                sms_time=sms_time,
                customer_note=customer_note,
                service_notice=service_notice,
                status_value="",
                staff=meta.get("服務人員", "無人力"),
                service_status=meta.get("服務狀態", "未處理"),
                fare=meta.get("車馬費", "0"),
            )

            if existing_order_no and has_action(selected_actions, "寄確認信"):
                result.update(stage_send_confirmation(existing_order_no, session))

            if has_action(selected_actions, "改 Google 日曆"):
                calendar_info = stage_calendar_color(detail["row"], gcal_service, region)
                result.update(calendar_info)
                if existing_order_no:
                    result.update(stage_update_status(existing_order_no, result, calendar_info, result))

            row_results[detail["row_num"]] = result

        return row_results

    no_slot_dates = []
    valid_details = []

    for detail in row_details:
        raw = get_section_raw(session, detail["payload"], token, detail["slot"])
        slot_ok = slot_exists_in_section_response(raw, detail["slot"])
        cleaners = extract_cleaners_from_section_response(raw, detail["slot"])

        # v2026-07：查無班表時，若客服有勾選「查無班表時自動補檸檬人排班」
        # （allow_auto_lemon_shift），才嘗試補勾檸檬人班表後重查一次；
        # 未勾選則維持原行為，直接標記為「無班表」。與舊客/新客/訂單轉換/
        # 儲值金補價差四個成單功能的行為保持一致。
        if not slot_ok and allow_auto_lemon_shift:
            try:
                ensure_lemon_cleaner_shifts(
                    session=session, base_url=BASE_URL,
                    service_date=detail["date"], period_s=system_period,
                    person_count=str(people),
                )
                time.sleep(2)
                raw = get_section_raw(session, detail["payload"], token, detail["slot"])
                slot_ok = slot_exists_in_section_response(raw, detail["slot"])
                cleaners = extract_cleaners_from_section_response(raw, detail["slot"])
            except Exception as _e_lemon:
                print(f"[DEBUG] 自動補檸檬人排班失敗：{_e_lemon}")

        detail["section_cleaners"] = cleaners
        detail["section_staff"] = format_staff_from_cleaners(cleaners, people=people)

        print("[DEBUG] section match =", {
            "slot": detail["slot"],
            "matched": slot_ok,
            "staff": detail.get("section_staff"),
            "raw_preview": str(raw)[:500],
        })
        try:
            if st is not None:
                st.write("🧩 section match =", {
                    "slot": detail["slot"],
                    "matched": slot_ok,
                    "staff": detail.get("section_staff"),
                    "raw_preview": str(raw)[:500],
                })
        except Exception:
            pass

        if slot_ok:
            valid_details.append(detail)
        else:
            no_slot_dates.append(detail["date"])

    if not valid_details:
        for detail in row_details:
            sms_time, customer_note = build_time_fields()
            service_notice = str(detail["payload"].get("notice") or "")
            row_results[detail["row_num"]] = build_row_result(
                result="失敗",
                reason="無班表",
                no_slot_date=detail["date"],
                sms_time=sms_time,
                customer_note=customer_note,
                service_notice=service_notice,
                status_value="",
                staff="無人力",
                service_status="未處理",
                fare="0",
            )
        return row_results

    # 不再預設檢查「儲值金餘額是否足夠訂單金額」。
    # 後台系統本身已有檢查，這裡只要有班表就送出。
    insufficient_dates = []
    send_details = valid_details

    for detail in row_details:
        sms_time, customer_note = build_time_fields()
        service_notice = str(detail["payload"].get("notice") or "")

        if detail["date"] in no_slot_dates:
            row_results[detail["row_num"]] = build_row_result(
                result="失敗",
                reason="無班表",
                no_slot_date=detail["date"],
                sms_time=sms_time,
                customer_note=customer_note,
                service_notice=service_notice,
                status_value="",
                staff="無人力",
                service_status="未處理",
                fare="0",
            )
        elif detail["date"] in insufficient_dates:
            row_results[detail["row_num"]] = build_row_result(
                result="未送",
                reason="餘額不足",
                insufficient_date=detail["date"],
                sms_time=sms_time,
                customer_note=customer_note,
                service_notice=service_notice,
                status_value="",
                staff=detail.get("section_staff") or "無人力",
                service_status="未處理",
                fare=str(detail["payload"].get("fare") or "0"),
            )

    if not send_details:
        return row_results

    # v2026-07：追蹤「本次呼叫」已配對過的訂單編號，避免比對日期+時段+電話時
    # 誤配對到本次批次自己前面幾筆剛建立、但實際上不同列的訂單。
    if used_order_nos is None:
        used_order_nos = set()

    # 每筆單獨送出，避免日期互相污染
    for detail in send_details:
        payload = detail["payload"].copy()
        slots = [detail["slot"]]

        print("[DEBUG] booking payload =",
              {
                  "date": detail["date"],
                  "slot": detail["slot"],
                  "price": payload.get("price"),
                  "fare": payload.get("fare"),
                  "addressId": payload.get("addressId"),
                  "area_id": payload.get("area_id"),
                  "company_id": payload.get("company_id"),
                  "notice_len": len(str(payload.get("notice") or "")),
              })

        session.post(
            BOOKING_URL,
            data={**payload, "_token": token, "date_list[]": slots},
            headers=HEADERS,
            allow_redirects=True,
        )

        time.sleep(1)

        # v2026-07：比對時同時帶入電話 + 已排除本次用過的訂單編號，避免
        # 只用日期+時段配對到別人的訂單，造成同一個訂單編號被誤寫進兩列
        # Google Sheet（M欄重複、實際上只有一列真的成單）。
        order_no = fetch_order_no_by_date_and_period(
            session, detail["date"], detail["display_period"],
            phone=phone, exclude_order_nos=used_order_nos,
        )
        if order_no:
            used_order_nos.add(order_no)
        sms_time, customer_note = build_time_fields()
        service_notice = str(payload.get("notice") or "")

        if not order_no:
            row_results[detail["row_num"]] = build_row_result(
                result="已送出",
                reason="抓不到訂單編號",
                sms_time=sms_time,
                customer_note=customer_note,
                service_notice=service_notice,
                status_value="",
                staff=detail.get("section_staff") or "無人力",
                service_status="未處理",
                fare=str(detail["payload"].get("fare") or "0"),
            )
            continue

        meta = fetch_order_meta_by_order_no(session, order_no)

        staff_value = meta.get("服務人員", "")
        if not staff_value or staff_value == "無人力":
            staff_value = detail.get("section_staff") or "無人力"

        stage_result = build_row_result(
            order_no=order_no,
            result="成功",
            reason="",
            sms_time=sms_time,
            customer_note=customer_note,
            service_notice=service_notice,
            status_value="",
            staff=staff_value,
            service_status=meta.get("服務狀態", "未處理"),
            fare=meta.get("車馬費", "0") or str(detail["payload"].get("fare") or "0"),
        )

        confirm_info = {}
        calendar_info = {}

        if has_action(selected_actions, "寄確認信"):
            confirm_info = stage_send_confirmation(order_no, session)
            stage_result.update(confirm_info)

        if has_action(selected_actions, "改 Google 日曆"):
            calendar_info = stage_calendar_color(detail["row"], gcal_service, region)
            stage_result.update(calendar_info)

        stage_result.update(stage_update_status(order_no, confirm_info, calendar_info, stage_result))

        row_results[detail["row_num"]] = stage_result

    return row_results


# =========================
# 主執行
# =========================
def run_process(sheet_name, start_row, end_row, env_name_from_ui=None, allow_auto_lemon_shift=False):
    print(f"目前環境：{ENV}")
    print(f"BASE_URL：{BASE_URL}")
    print(f"執行工作表：{sheet_name}")
    print(f"執行列範圍：{start_row} ~ {end_row}")

    ws, df = load_worksheet(sheet_name)

    required_cols = [
        "服務人時",
        "備註",
        "姓名",
        "電話",
        "地址",
        "日期",
        "開始時間",
        "結束時間",
        "狀態",
        "購買項目",
        "訂單編號",
    ]
    for col in required_cols:
        if col not in df.columns:
            raise Exception(f"工作表缺少必要欄位: {col}")

    df = df[(df["__sheet_row__"] >= start_row) & (df["__sheet_row__"] <= end_row)]
    df = df[df.apply(should_process_row, axis=1)]

    if df.empty:
        print("沒有符合條件的資料可執行。")
        return

    gcal_service = None
    if ENABLE_GCAL_COLOR_SYNC:
        try:
            gcal_service = build_gcal_service()
            print("Google Calendar 已啟用")
        except Exception as e:
            print(f"Google Calendar 初始化失敗：{e}")
            gcal_service = None

    grouped_orders = defaultdict(list)

    for _, row in df.iterrows():
        region = get_region_by_address(str(row["地址"]), ACCOUNTS)
        if not region:
            continue
        if not should_create_order(row):
            continue

        key = (region, build_group_key(row))
        grouped_orders[key].append((int(row["__sheet_row__"]), row))

    all_row_results = {}

    region_groups = defaultdict(list)
    for (region, group_key), items in grouped_orders.items():
        region_groups[region].append((group_key, items))

    for region, group_items in region_groups.items():
        config = ACCOUNTS.get(region)
        if not config:
            continue

        email = config["email"]
        password = config["password"]

        print(f"\n===== 開始處理區域：{region} ({email}) =====")

        session = requests.Session()
        if not login(session, email, password):
            print("登入失敗，略過該區域")
            continue

        used_order_nos_this_region = set()

        for group_no, (_, rows_with_idx) in enumerate(group_items, start=1):
            _, first_row = rows_with_idx[0]
            print(f"\n--- 處理第 {group_no} 組：{first_row['姓名']}，共 {len(rows_with_idx)} 筆 ---")

            try:
                token = get_csrf_token(session)
                row_results = process_one_group(
                    session,
                    rows_with_idx,
                    token,
                    gcal_service,
                    region,
                    None,
                    ["建單", "寄確認信", "改 Google 日曆"],
                    allow_auto_lemon_shift=allow_auto_lemon_shift,
                    used_order_nos=used_order_nos_this_region,
                )
                all_row_results.update(row_results)
            except Exception as e:
                print(f"❌ 整組失敗：{e}")
                for row_num, _ in rows_with_idx:
                    all_row_results[row_num] = build_row_result(
                        result="失敗",
                        reason=str(e),
                        status_value="",
                        staff="無人力",
                        service_status="未處理",
                        fare="0",
                    )

            time.sleep(REQUEST_DELAY)

    update_sheet_rows(ws, all_row_results)
    print("已回填 Google Sheet。")

    try:
        consistency_problems = verify_batch_order_consistency(session, df, all_row_results)
        if consistency_problems:
            print(f"⚠️ 訂單一致性檢查發現 {len(consistency_problems)} 筆異常")
            for p in consistency_problems:
                _row_label = f"第 {p['row_num']} 列" if p.get("row_num") is not None else "（系統反查）"
                print(f"  {_row_label}：{p['issue']}")
    except Exception as e:
        print(f"訂單一致性檢查失敗：{e}")


def get_runtime_config(env_name: str):
    if env_name == "dev":
        return {
            "BASE_URL": BASE_URL_DEV,
            "ORDER_PREFIX": ORDER_PREFIX_DEV,
        }
    return {
        "BASE_URL": BASE_URL_PROD,
        "ORDER_PREFIX": ORDER_PREFIX_PROD,
    }


def run_process_web(env_name, region, backend_email, backend_password, sheet_name, start_row, end_row, selected_actions=None, logger=print, allow_auto_lemon_shift=False):
    global BASE_URL, ORDER_PREFIX
    if env_name == "dev":
        BASE_URL = BASE_URL_DEV
        ORDER_PREFIX = ORDER_PREFIX_DEV
    else:
        BASE_URL = BASE_URL_PROD
        ORDER_PREFIX = ORDER_PREFIX_PROD

    global LOGIN_URL, BOOKING_URL, PURCHASE_URL, GET_MEMBER_URL
    global CHECK_CONTAIN_URL, CALCULATE_HOUR_URL, GET_SECTION_URL, MAIL_SUCCESS_URL

    LOGIN_URL = f"{BASE_URL}/login"
    BOOKING_URL = f"{BASE_URL}/booking/stored_value_routine"
    PURCHASE_URL = f"{BASE_URL}/purchase"
    GET_MEMBER_URL = f"{BASE_URL}/ajax/get_member"
    CHECK_CONTAIN_URL = f"{BASE_URL}/ajax/check_contain"
    CALCULATE_HOUR_URL = f"{BASE_URL}/ajax/calculate_hour"
    GET_SECTION_URL = f"{BASE_URL}/ajax/get_section"
    MAIL_SUCCESS_URL = f"{BASE_URL}/purchase/mail_success/{{order_no}}"

    logger(f"目前環境：{env_name}")
    logger(f"BASE_URL：{BASE_URL}")
    logger(f"執行區域：{region}")
    logger(f"執行工作表：{sheet_name}")
    logger(f"執行列範圍：{start_row} ~ {end_row}")

    if selected_actions is None:
        selected_actions = ["建單", "寄確認信", "改 Google 日曆"]

    ws, df = load_worksheet(sheet_name)

    required_cols = [
        "服務人時",
        "備註",
        "姓名",
        "電話",
        "地址",
        "日期",
        "開始時間",
        "結束時間",
        "狀態",
        "購買項目",
        "訂單編號",
    ]
    for col in required_cols:
        if col not in df.columns:
            raise Exception(f"工作表缺少必要欄位: {col}")

    df = df[(df["__sheet_row__"] >= start_row) & (df["__sheet_row__"] <= end_row)]
    df = df[df.apply(should_process_row, axis=1)]

    if df.empty:
        logger("沒有符合條件的資料可執行。")
        return {
            "success": True,
            "message": "沒有符合條件的資料",
            "failed_records": [],
        }

    filtered_rows = [row for _, row in df.iterrows() if get_region_by_address(str(row["地址"]), ACCOUNTS) == region]
    if not filtered_rows:
        logger(f"沒有 {region} 區域的資料可執行。")
        return {
            "success": True,
            "message": f"沒有 {region} 區域資料",
            "failed_records": [],
        }

    df = pd.DataFrame(filtered_rows)
    if "__sheet_row__" not in df.columns:
        raise Exception("資料缺少 __sheet_row__")

    gcal_service = None
    if ENABLE_GCAL_COLOR_SYNC:
        try:
            gcal_service = build_gcal_service()
            logger("Google Calendar 已啟用")
        except Exception as e:
            logger(f"Google Calendar 初始化失敗：{e}")
            gcal_service = None

    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼")

    grouped_orders = defaultdict(list)
    existing_order_rows = []

    for _, row in df.iterrows():
        row_num = int(row["__sheet_row__"])

        if not has_action(selected_actions, "建單") or not should_create_order(row):
            existing_order_rows.append((row_num, row))
            continue

        grouped_orders[build_group_key(row)].append((row_num, row))

    all_row_results = {}
    failed_records = []

    for row_num, row in existing_order_rows:
        try:
            result = process_existing_order_only(row, gcal_service, region, session, selected_actions)
            all_row_results[row_num] = result
            if result.get("結果") == "失敗":
                failed_records.append({
                    "row": row_num,
                    "name": str(row.get("姓名", "未知")).strip(),
                    "error": str(result.get("原因", "")),
                })
        except Exception as e:
            all_row_results[row_num] = build_row_result(
                result="失敗",
                reason=f"補處理失敗: {e}",
                status_value="",
                staff="無人力",
                service_status="未處理",
                fare="0",
            )
            failed_records.append({
                "row": row_num,
                "name": str(row.get("姓名", "未知")).strip(),
                "error": f"補處理失敗: {e}",
            })

    # v2026-07：本次呼叫累計已配對過的訂單編號，避免跨組別誤配對到同一張訂單
    used_order_nos_this_run = set()

    for group_no, (_, rows_with_idx) in enumerate(grouped_orders.items(), start=1):
        _, first_row = rows_with_idx[0]
        logger(f"處理第 {group_no} 組：{first_row['姓名']}，共 {len(rows_with_idx)} 筆")

        try:
            token = get_csrf_token(session)
            row_results = process_one_group(
                session, rows_with_idx, token, gcal_service, region, None, selected_actions,
                allow_auto_lemon_shift=allow_auto_lemon_shift,
                used_order_nos=used_order_nos_this_run,
            )
            all_row_results.update(row_results)

            for row_num, row in rows_with_idx:
                result = row_results.get(row_num, {})
                if result.get("結果") == "失敗":
                    failed_records.append({
                        "row": row_num,
                        "name": str(row.get("姓名", "未知")).strip(),
                        "error": str(result.get("原因", "")),
                    })
        except Exception as e:
            logger(f"整組失敗：{e}")
            for row_num, row in rows_with_idx:
                failed_records.append({
                    "row": row_num,
                    "name": str(row.get("姓名", "未知")).strip(),
                    "error": str(e),
                })
                all_row_results[row_num] = build_row_result(
                    result="失敗",
                    reason=str(e),
                    status_value="",
                    staff="無人力",
                    service_status="未處理",
                    fare="0",
                )

        time.sleep(REQUEST_DELAY)

    update_sheet_rows(ws, all_row_results)
    logger("已回填 Google Sheet。")

    # v2026.07.05：一致性檢查改由呼叫端（ordersapp.py）在「整批列都執行完」後，
    # 用 run_batch_consistency_check 統一做一次，而不是每呼叫一次 run_process_web
    # 就各自比對一次（原本的寫法會讓同一支電話被重複查詢很多次，也不是真正
    # 「全部成單到一個段落後」的整批核對）。這裡不再自動觸發。
    success_count = sum(1 for v in all_row_results.values() if v.get("結果") == "成功")
    fail_count = sum(1 for v in all_row_results.values() if v.get("結果") == "失敗")

    return {
        "success": True,
        "sheet_name": sheet_name,
        "region": region,
        "env": env_name,
        "success_count": success_count,
        "fail_count": fail_count,
        "total_processed": len(all_row_results),
        "failed_records": failed_records,
    }


def run_batch_consistency_check(env_name, region, backend_email, backend_password, sheet_name, target_rows, logger=print):
    """
    v2026.07.05：批次「整批列都執行完」之後，統一做一次雙向一致性比對，
    取代原本掛在 run_process_web 裡、每呼叫一次就各自比對一次的寫法
    （那樣同一支電話在多列批次裡會被重複查詢很多次，也不是真正「全部成單到
    一個段落後」的整批核對）。

    做法：重新讀一次 Google Sheet 目前的狀態（此時 M 欄等欄位應該都已經是
    這批次執行完、回填後的最終結果），只取 target_rows 這些列、且屬於
    region 這個區域的資料，組成 verify_batch_order_consistency 需要的
    all_row_results（每一列目前 Sheet 上寫的訂單編號），再呼叫既有的雙向
    比對邏輯。

    target_rows: 這次批次實際跑過的列號，可以是不連續的 list，例如 [2, 5, 9]。
    回傳 list of dict：[{row_num, order_no, issue}, ...]，沒有問題則回傳空 list。
    """
    global BASE_URL, ORDER_PREFIX
    if env_name == "dev":
        BASE_URL = BASE_URL_DEV
        ORDER_PREFIX = ORDER_PREFIX_DEV
    else:
        BASE_URL = BASE_URL_PROD
        ORDER_PREFIX = ORDER_PREFIX_PROD

    global LOGIN_URL, BOOKING_URL, PURCHASE_URL, GET_MEMBER_URL
    global CHECK_CONTAIN_URL, CALCULATE_HOUR_URL, GET_SECTION_URL, MAIL_SUCCESS_URL

    LOGIN_URL = f"{BASE_URL}/login"
    BOOKING_URL = f"{BASE_URL}/booking/stored_value_routine"
    PURCHASE_URL = f"{BASE_URL}/purchase"
    GET_MEMBER_URL = f"{BASE_URL}/ajax/get_member"
    CHECK_CONTAIN_URL = f"{BASE_URL}/ajax/check_contain"
    CALCULATE_HOUR_URL = f"{BASE_URL}/ajax/calculate_hour"
    GET_SECTION_URL = f"{BASE_URL}/ajax/get_section"
    MAIL_SUCCESS_URL = f"{BASE_URL}/purchase/mail_success/{{order_no}}"

    target_row_set = {int(r) for r in (target_rows or [])}
    if not target_row_set:
        return []

    ws, df = load_worksheet(sheet_name)
    required_cols = ["電話", "地址", "日期", "開始時間", "結束時間", "訂單編號"]
    for col in required_cols:
        if col not in df.columns:
            raise Exception(f"工作表缺少必要欄位: {col}")

    df = df[df["__sheet_row__"].isin(target_row_set)]
    if df.empty:
        logger("一致性檢查：指定的列號在工作表裡查無資料，略過。")
        return []

    df = df[df.apply(lambda row: get_region_by_address(str(row["地址"]), ACCOUNTS) == region, axis=1)]
    if df.empty:
        logger(f"一致性檢查：指定的列號裡沒有屬於 {region} 區域的資料，略過。")
        return []

    session = requests.Session()
    if not login(session, backend_email, backend_password):
        raise Exception("後台登入失敗，請確認帳號密碼（一致性檢查階段）")

    all_row_results = {}
    for _, row in df.iterrows():
        row_num = int(row["__sheet_row__"])
        all_row_results[row_num] = {"訂單編號": str(row.get("訂單編號", "") or "").strip()}

    logger(f"開始整批一致性檢查（共 {len(all_row_results)} 列）…")
    problems = verify_batch_order_consistency(session, df, all_row_results)

    if problems:
        logger(f"⚠️ 訂單一致性檢查發現 {len(problems)} 筆異常，請人工確認：")
        for p in problems:
            _row_label = f"第 {p['row_num']} 列" if p.get("row_num") is not None else "（系統反查）"
            logger(f"  {_row_label}（訂單 {p['order_no']}）：{p['issue']}")
    else:
        logger("✅ 訂單一致性檢查通過，本次寫回的訂單編號皆與 Google Sheet 電話/地址/日期/時段相符。")

    return problems
