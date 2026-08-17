from __future__ import annotations

# ============================================================
# tools/field_management/resume_system.py
#
# 從「履歷系統.gs」（GAS，📄 履歷系統選單）移植而來：
#   1. 擷取 104履歷（依起始時間 / 指定日期範圍）
#   2. 擷取 104回覆信（LEMON HOME 回覆信）
#   3. 擷取最新雙北履歷（未讀信＋地區關鍵字過濾）
#
# GAS 用 GmailApp（容器綁定帳號）讀信；tool-system 是無介面的排程/後台服務，
# 沒有「目前登入的 Gmail 帳號」這個概念，改用 IMAP（imaplib，與
# tools/service_management/service_schedule.py 的 Gmail 讀信方式一致）
# 搭配 App 密碼登入指定信箱。
#
# 必要 Secrets（環境變數或 Streamlit secrets 皆可）：
#   RESUME_GMAIL_USER / RESUME_GMAIL_APP_PASSWORD
#     （若未設定，fallback 使用既有的 GMAIL_USER / GMAIL_APP_PASSWORD）
#
# GAS 原本用 PropertiesService 記住「共用 Google Sheet ID」與「使用區域」
# （setSharedId / setResumeArea）；tool-system 的名冊試算表 ID 已經在
# config/systems.yaml 的 spreadsheet_ids.roster.{area} 設定好了，所以這裡
# 直接用呼叫端傳入的 area 參數解析目標試算表，不再需要額外的持久化設定。
# ============================================================

import argparse
import email
import email.utils
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from typing import Any
from urllib.parse import quote

try:
    from .roster_raise import (
        append_values,
        find_sheet_props,
        get_sheet_props_list,
        get_values,
    )
except ImportError:
    from roster_raise import (
        append_values,
        find_sheet_props,
        get_sheet_props_list,
        get_values,
    )

try:
    from .staff_profile import get_sheets_service, get_spreadsheet_id, load_system_config
except ImportError:
    from staff_profile import get_sheets_service, get_spreadsheet_id, load_system_config

try:
    from .logger import log
except ImportError:
    from logger import log

from tools.common.log_to_sheet import write_target_log


TZ = timezone(timedelta(hours=8))
SYSTEM_NAME = "外場排程系統"
RESUME_SHEET_NAME = "104應徵履歷"
REPLY_SHEET_NAME = "104回覆紀錄"

RESUME_SUBJECT = "104應徵履歷"
REPLY_SUBJECT = "Re:【LEMON HOME檸檬家事_檸檬專業清潔有限公司】"
REPLY_KEY_PHRASE = "您好，我有興趣了解貴公司職缺細節"

EXCLUDE_SUBJECT_KEYWORDS = ["內勤", "助理", "客服", "督導", "經理"]
INCLUDE_REGION_KEYWORDS = [
    "台北", "臺北", "新北", "雙北", "淡水", "板橋", "中和", "永和", "新莊", "三重",
    "蘆洲", "土城", "三峽", "樹林", "汐止", "林口", "泰山", "五股", "八里", "新店",
    "大安", "信義", "中山", "松山", "中正", "萬華", "大同", "南港", "內湖", "士林",
    "北投", "文山",
]
# 沿用 GAS 原始邏輯中的手動排除名單（誤入的舊資料）。
HARD_EXCLUDE_NAMES = ["陳柏宏"]

_WEEKDAY_TEXT = ["日", "一", "二", "三", "四", "五", "六"]

_NAME_FROM_BODY_AGE = re.compile(r"([^\s\d<]{2,30})\s*\d{1,2}歲")
_NAME_FROM_SUBJECT = re.compile(r"^(.{2,30})（")
_NAME_FROM_GREETING = re.compile(r"您好，我叫(.{2,30})[，。]")
_NAME_FROM_SUBJECT_104 = re.compile(r"應徵履歷_([^_]+)_")
_REGION_BRACKETS = re.compile(r"【([^【】\r\n]+?)】")
_PHONE_PATTERN = re.compile(r"09\d{2}-?\d{3}-?\d{3}")
_PHONE_LABELED_PATTERN = re.compile(r"聯絡電話[\s:]*(09[0-9\-\s]{8,12})")
_EMAIL_PATTERN = re.compile(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}")
_REPLY_NAME_PATTERN = re.compile(r"([一-龥]{2,6})\s*20\d{2}/\d{2}/\d{2}")


# ────────────────────────────────────────────────────────────
# Secrets / IMAP
# ────────────────────────────────────────────────────────────

def _get_secret(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value

    try:
        import streamlit as st

        value = str(st.secrets.get(key, "") or "").strip()
        if value:
            return value
    except Exception:
        pass

    return ""


def _imap_connect() -> imaplib.IMAP4_SSL:
    user = _get_secret("RESUME_GMAIL_USER") or _get_secret("GMAIL_USER")
    password = _get_secret("RESUME_GMAIL_APP_PASSWORD") or _get_secret("GMAIL_APP_PASSWORD")

    if not user or not password:
        raise EnvironmentError("需要 RESUME_GMAIL_USER 和 RESUME_GMAIL_APP_PASSWORD（或 GMAIL_USER / GMAIL_APP_PASSWORD）")

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password)
    return imap


def _search(imap: imaplib.IMAP4_SSL, criteria: str) -> list[bytes]:
    try:
        typ, data = imap.search("UTF-8", criteria.encode("utf-8"))
    except Exception:
        try:
            typ, data = imap.search(None, criteria)
        except Exception:
            return []

    if typ != "OK" or not data or not data[0]:
        return []

    return data[0].split()


def _fetch_message(imap: imaplib.IMAP4_SSL, num: bytes) -> email.message.Message | None:
    typ, msg_data = imap.fetch(num, "(RFC822)")
    if typ != "OK" or not msg_data or not msg_data[0]:
        return None
    return email.message_from_bytes(msg_data[0][1])


def _add_label(imap: imaplib.IMAP4_SSL, num: bytes, label: str) -> None:
    try:
        imap.store(num, "+X-GM-LABELS", f"({label})")
    except Exception:
        pass


def _decode_subject(msg: email.message.Message) -> str:
    parts = decode_header(msg.get("Subject", "") or "")
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += str(part)
    return result


def _strip_html(html_text: str) -> str:
    import html as html_module

    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</div>|</tr>|</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\r\n|\r", "\n", text)
    return text.strip()


def _plain_body(msg: email.message.Message) -> str:
    plain = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/plain" and not plain:
                plain = decoded
            elif content_type == "text/html" and not html_body:
                html_body = decoded
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                plain = decoded

    if plain.strip():
        return plain
    if html_body.strip():
        return _strip_html(html_body)
    return ""


def _message_datetime(msg: email.message.Message) -> datetime:
    date_tuple = email.utils.parsedate_tz(msg.get("Date", "") or "")
    if not date_tuple:
        return datetime.now(TZ)
    ts = email.utils.mktime_tz(date_tuple)
    return datetime.fromtimestamp(ts, TZ)


def _mail_link(msg: email.message.Message) -> str:
    message_id = str(msg.get("Message-ID", "") or "").strip().strip("<>")
    if not message_id:
        return ""
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{quote(message_id)}"


def _ensure_sheet_exists(sheets, spreadsheet_id: str, sheet_name: str) -> None:
    props_list = get_sheet_props_list(sheets, spreadsheet_id)
    if not find_sheet_props(props_list, sheet_name):
        raise RuntimeError(f"找不到工作表「{sheet_name}」")


# ────────────────────────────────────────────────────────────
# 欄位擷取
# ────────────────────────────────────────────────────────────

def _extract_name(content: str, subject: str) -> str:
    match = (
        _NAME_FROM_BODY_AGE.search(content)
        or _NAME_FROM_SUBJECT.match(subject)
        or _NAME_FROM_GREETING.search(content)
    )
    if match:
        return match.group(1).strip()

    subj_match = _NAME_FROM_SUBJECT_104.search(subject)
    if subj_match:
        return subj_match.group(1).strip()

    return ""


def _extract_region(subject: str) -> str:
    matches = _REGION_BRACKETS.findall(subject)
    if not matches:
        return ""
    last = matches[-1]
    parts = re.split(r"[、,，/]", last)
    return "/".join(p.strip() for p in parts if p.strip())


def _extract_phone(content: str) -> str:
    match = _PHONE_PATTERN.search(content) or _PHONE_LABELED_PATTERN.search(content)
    if not match:
        return ""
    return re.sub(r"[^\d]", "", match.group(0))


def _extract_email(content: str) -> str:
    match = _EMAIL_PATTERN.search(content)
    return match.group(0) if match else ""


def _format_phone_for_sheet(digits: str) -> str:
    if len(digits) == 10:
        return "'" + digits[0:4] + "-" + digits[4:7] + "-" + digits[7:10]
    return "'" + digits


# ────────────────────────────────────────────────────────────
# 1. 擷取 104履歷（依時間區間）
# ────────────────────────────────────────────────────────────

def fetch_resumes_range(area: str, start_dt: datetime, end_dt: datetime, run_type: str = "手動") -> dict[str, Any]:
    cfg = load_system_config(SYSTEM_NAME)
    spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    status = "失敗"
    message = ""
    count = 0

    try:
        sheets = get_sheets_service()
        _ensure_sheet_exists(sheets, spreadsheet_id, RESUME_SHEET_NAME)

        imap = _imap_connect()
        try:
            imap.select("INBOX")

            since_str = start_dt.strftime("%d-%b-%Y")
            before_str = (end_dt + timedelta(days=1)).strftime("%d-%b-%Y")
            criteria = f'(SUBJECT "{RESUME_SUBJECT}" SINCE {since_str} BEFORE {before_str})'
            nums = _search(imap, criteria)

            entries: list[tuple[datetime, list[Any]]] = []

            for num in nums:
                msg = _fetch_message(imap, num)
                if msg is None:
                    continue

                msg_dt = _message_datetime(msg)
                if msg_dt < start_dt or msg_dt > end_dt:
                    continue

                subject = _decode_subject(msg)
                raw_body = _plain_body(msg)
                content = _strip_html(raw_body) if "<" in raw_body else raw_body

                name = _extract_name(content, subject)
                region = _extract_region(subject)
                phone_digits = _extract_phone(content)
                email_addr = _extract_email(content)
                link = _mail_link(msg)

                row = [
                    msg_dt.strftime("%Y/%m/%d %H:%M:%S"),
                    _WEEKDAY_TEXT[msg_dt.isoweekday() % 7],
                    link,
                    region,
                    name,
                    ("'" + phone_digits) if phone_digits else "",
                    email_addr,
                ]
                entries.append((msg_dt, row))
        finally:
            try:
                imap.logout()
            except Exception:
                pass

        entries.sort(key=lambda item: item[0])
        rows = [row for _, row in entries]

        append_values(sheets, spreadsheet_id, f"'{RESUME_SHEET_NAME}'!A1", rows)

        count = len(rows)
        status = "成功"
        message = (
            f"區間={start_dt.strftime('%Y/%m/%d %H:%M:%S')} ~ {end_dt.strftime('%Y/%m/%d %H:%M:%S')}"
            f"｜新增 {count} 筆"
        )
        log(f"完成擷取104履歷：{message}")

        return {"area": area, "count": count}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"擷取104履歷失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="擷取104履歷",
            run_type=run_type,
            area=area,
            date=datetime.now(TZ).strftime("%Y-%m-%d"),
            target_location=RESUME_SHEET_NAME,
            source_file=f'Gmail subject:"{RESUME_SUBJECT}"',
            status=status,
            message=message,
        )


# ────────────────────────────────────────────────────────────
# 2. 擷取 104回覆信
# ────────────────────────────────────────────────────────────

def fetch_lemon_home_replies(area: str, run_type: str = "手動") -> dict[str, Any]:
    cfg = load_system_config(SYSTEM_NAME)
    spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    status = "失敗"
    message = ""
    count = 0

    try:
        sheets = get_sheets_service()
        _ensure_sheet_exists(sheets, spreadsheet_id, REPLY_SHEET_NAME)

        imap = _imap_connect()
        try:
            imap.select("INBOX")

            criteria = f'(SUBJECT "{REPLY_SUBJECT}")'
            nums = _search(imap, criteria)

            rows: list[list[Any]] = []

            for num in nums:
                msg = _fetch_message(imap, num)
                if msg is None:
                    continue

                body = _plain_body(msg)
                body = _strip_html(body) if "<" in body else body

                if REPLY_KEY_PHRASE not in body:
                    continue

                msg_dt = _message_datetime(msg)
                subject = _decode_subject(msg)

                name_match = _REPLY_NAME_PATTERN.search(body)
                name = name_match.group(1) if name_match else ""

                regions = _REGION_BRACKETS.findall(subject) or _REGION_BRACKETS.findall(body)
                region = "/".join(regions) if regions else ""

                link = _mail_link(msg)

                rows.append([
                    msg_dt.strftime("%Y/%m/%d %H:%M:%S"),
                    name,
                    REPLY_KEY_PHRASE + "，謝謝。",
                    region,
                    link,
                ])

                _add_label(imap, num, "已登記")
        finally:
            try:
                imap.logout()
            except Exception:
                pass

        append_values(sheets, spreadsheet_id, f"'{REPLY_SHEET_NAME}'!A1", rows)

        count = len(rows)
        status = "成功"
        message = f"新增 {count} 筆"
        log(f"完成擷取104回覆信：{message}")

        return {"area": area, "count": count}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"擷取104回覆信失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="擷取104回覆信",
            run_type=run_type,
            area=area,
            date=datetime.now(TZ).strftime("%Y-%m-%d"),
            target_location=REPLY_SHEET_NAME,
            source_file=f'Gmail subject:"{REPLY_SUBJECT}"',
            status=status,
            message=message,
        )


# ────────────────────────────────────────────────────────────
# 3. 擷取最新雙北履歷（未讀信 + 地區關鍵字過濾）
# ────────────────────────────────────────────────────────────

def extract_latest_resumes(area: str, target_sheet_name: str, run_type: str = "手動") -> dict[str, Any]:
    """
    對應 GAS 的 extract104Resumes()。GAS 版本寫入「目前作用中的工作表」
    （SpreadsheetApp.getActiveSpreadsheet().getActiveSheet()）；tool-system
    是無畫面的後台服務，沒有「目前作用中工作表」的概念，因此改為明確傳入
    target_sheet_name（由 UI 讓使用者選擇要寫入哪個分頁，例如當期的
    「XXX專員名冊」或既有的履歷追蹤表）。
    """
    cfg = load_system_config(SYSTEM_NAME)
    spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    status = "失敗"
    message = ""
    count = 0

    try:
        sheets = get_sheets_service()
        _ensure_sheet_exists(sheets, spreadsheet_id, target_sheet_name)

        existing = get_values(sheets, spreadsheet_id, f"'{target_sheet_name}'!D:E")
        existing_records = set()
        for row in existing:
            name = str(row[0]).strip() if len(row) > 0 else ""
            phone = re.sub(r"[^\d]", "", str(row[1])) if len(row) > 1 else ""
            if name or phone:
                existing_records.add(f"{name}|{phone}")

        imap = _imap_connect()
        rows: list[list[Any]] = []

        try:
            imap.select("INBOX")

            criteria = f'(UNSEEN SUBJECT "{RESUME_SUBJECT}")'
            nums = _search(imap, criteria)

            for num in nums:
                msg = _fetch_message(imap, num)
                if msg is None:
                    continue

                subject = _decode_subject(msg)

                if any(kw in subject for kw in EXCLUDE_SUBJECT_KEYWORDS):
                    continue
                if not any(kw in subject for kw in INCLUDE_REGION_KEYWORDS):
                    continue

                content = _plain_body(msg)
                content = _strip_html(content) if "<" in content else content

                name = _extract_name(content, subject) or "需手動確認"
                name = re.sub(r"[【】「」>*\s]", "", name)

                if any(bad in name or bad in content or bad in subject for bad in HARD_EXCLUDE_NAMES):
                    continue

                phone_digits = _extract_phone(content)
                phone = _format_phone_for_sheet(phone_digits) if phone_digits else "需手動確認"

                record_key = f"{name}|{phone_digits}"
                if name == "需手動確認" and not phone_digits:
                    record_key += str(msg.get("Message-ID", ""))

                if record_key in existing_records:
                    continue

                msg_dt = _message_datetime(msg)

                rows.append([
                    msg_dt.strftime("%m/%d"),
                    _WEEKDAY_TEXT[msg_dt.isoweekday() % 7],
                    "104",
                    name,
                    phone,
                    True,
                    False,
                    "", "", "", "", "", "",
                ])
                existing_records.add(record_key)

                _add_label(imap, num, "已自動匯出名單")
        finally:
            try:
                imap.logout()
            except Exception:
                pass

        append_values(sheets, spreadsheet_id, f"'{target_sheet_name}'!A1", rows)

        count = len(rows)
        status = "成功"
        message = f"新增 {count} 筆"
        log(f"完成擷取最新雙北履歷：{message}")

        return {"area": area, "count": count}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"擷取最新雙北履歷失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="擷取最新雙北履歷",
            run_type=run_type,
            area=area,
            date=datetime.now(TZ).strftime("%Y-%m-%d"),
            target_location=target_sheet_name,
            source_file=f'Gmail is:unread subject:"{RESUME_SUBJECT}"',
            status=status,
            message=message,
        )


# ────────────────────────────────────────────────────────────
# CLI（手動測試用；主要介面在 tools/field_management/ui.py）
# ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="外場排程系統：履歷系統")
    parser.add_argument("--action", required=True, choices=["fetch-range", "fetch-replies", "extract-latest"])
    parser.add_argument("--area", required=True, choices=["台北", "台中"])
    parser.add_argument("--start", help="起始時間，格式：YYYY/MM/DD HH:MM:SS")
    parser.add_argument("--end", help="結束時間，格式：YYYY/MM/DD HH:MM:SS")
    parser.add_argument("--sheet", help="extract-latest 要寫入的工作表名稱")
    parser.add_argument("--run-type", default="手動")
    args = parser.parse_args()

    if args.action == "fetch-range":
        start_dt = datetime.strptime(args.start, "%Y/%m/%d %H:%M:%S").replace(tzinfo=TZ)
        end_dt = datetime.strptime(args.end, "%Y/%m/%d %H:%M:%S").replace(tzinfo=TZ)
        result = fetch_resumes_range(args.area, start_dt, end_dt, args.run_type)
    elif args.action == "fetch-replies":
        result = fetch_lemon_home_replies(args.area, args.run_type)
    else:
        if not args.sheet:
            raise SystemExit("extract-latest 需要 --sheet 參數")
        result = extract_latest_resumes(args.area, args.sheet, args.run_type)

    log(f"執行結果：{result}")


if __name__ == "__main__":
    main()
