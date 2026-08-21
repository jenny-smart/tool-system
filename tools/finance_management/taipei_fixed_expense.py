"""新增『台北固定費用請款』：每月期別（YYYYMM）批次新增 5 筆台北固定費用
請款記錄，寫入「內部請款設定」登記表指向的請款記錄／台北報表。

5 筆固定費用：
  1. Amazon Web Services（信件主旨 "Amazon Web Services Tax Invoice Available"
     的 PDF 附件，抓 AWS Service Charges 金額，換算台幣＋1.5%）
  2. 震旦行（信件主旨含「震旦集團電子發票加值中心通知信」的 PDF 附件，抓總計金額）
  3. 眾點（信件主旨含「各分店每月發票金額確認」，抓信件內文的發票金額總計）
  4. 辦公室租金（固定金額，不用查信）
  5. 辦公室管理費（固定金額，不用查信）

信件收發沿用 tools/gmail_401（IMAP + App 密碼）與
tools/field_management/resume_system.py 的讀信慣例；PDF 附件用 pdfplumber
解析文字後用關鍵字＋正規表示式抓金額。
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import io
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.header import decode_header
from typing import Any

from tools.bank_statement.internal_payment_registry import (
    PAYMENT_REQUEST_TYPE,
    resolve_report_location,
)
from tools.common.config_loader import get_sheets_service
from tools.finance_management.execution_log import log_execution

TW_TZ = timezone(timedelta(hours=8))
AREA = "台北"
TOOL_NAME = "台北固定費用請款"

REQUEST_SHEET_RANGE = "A:I"

AWS_SUBJECT = "Amazon Web Services Tax Invoice Available"
ZHENDAN_SUBJECT = "震旦集團電子發票加值中心通知信"
ZHONGDIAN_SUBJECT = "各分店每月發票金額確認"

AWS_CHARGE_RE = re.compile(r"AWS Service Charges\s*USD\s*([\d,]+\.\d{2})", re.IGNORECASE)
FX_RATE_RE = re.compile(r"1\s*USD\s*=\s*([\d.]+)\s*TWD", re.IGNORECASE)
ZHENDAN_TOTAL_RE = re.compile(r"總計\s+([\d,]+)")
ZHONGDIAN_TOTAL_RE = re.compile(r"發票金額總計為\$?\s*([\d,]+)")

_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ────────────────────────────────────────────────────────────
# Secrets / IMAP（沿用 resume_system.py 的慣例）
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


def _imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{_IMAP_MONTHS[dt.month - 1]}-{dt.year:04d}"


def _imap_connect() -> tuple[imaplib.IMAP4_SSL, str]:
    user = (
        _get_secret("TAIPEI_FIXED_EXPENSE_GMAIL_USER")
        or _get_secret("GMAIL_401_USER")
        or _get_secret("GMAIL_USER")
    )
    password = (
        _get_secret("TAIPEI_FIXED_EXPENSE_GMAIL_APP_PASSWORD")
        or _get_secret("GMAIL_401_APP_PASSWORD")
        or _get_secret("GMAIL_APP_PASSWORD")
    )
    if not user or not password:
        raise EnvironmentError(
            "需要 TAIPEI_FIXED_EXPENSE_GMAIL_USER / TAIPEI_FIXED_EXPENSE_GMAIL_APP_PASSWORD"
            "（或既有的 GMAIL_401_USER / GMAIL_401_APP_PASSWORD）"
        )
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password)
    return imap, user


def _search(imap: imaplib.IMAP4_SSL, criteria: str) -> list[bytes]:
    typ, data = imap.search("UTF-8", criteria.encode("utf-8"))
    if typ != "OK":
        raise RuntimeError(f"IMAP 搜尋失敗（回應：{typ}）｜條件：{criteria}")
    if not data or not data[0]:
        return []
    return data[0].split()


def _fetch_message(imap: imaplib.IMAP4_SSL, num: bytes) -> email.message.Message | None:
    typ, msg_data = imap.fetch(num, "(RFC822)")
    if typ != "OK" or not msg_data or not msg_data[0]:
        return None
    return email.message_from_bytes(msg_data[0][1])


def _message_datetime(msg: email.message.Message) -> datetime:
    date_tuple = email.utils.parsedate_tz(msg.get("Date", "") or "")
    if not date_tuple:
        return datetime.now(TW_TZ)
    ts = email.utils.mktime_tz(date_tuple)
    return datetime.fromtimestamp(ts, TW_TZ)


def _decode_filename(part: email.message.Message) -> str:
    raw = part.get_filename()
    if not raw:
        return ""
    decoded_parts = decode_header(raw)
    filename = ""
    for chunk, charset in decoded_parts:
        if isinstance(chunk, bytes):
            filename += chunk.decode(charset or "utf-8", errors="replace")
        else:
            filename += chunk
    return filename


def _plain_body(msg: email.message.Message) -> str:
    plain, html_body = "", ""
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
    return _strip_html(html_body) if html_body.strip() else ""


def _strip_html(html_text: str) -> str:
    import html as html_module

    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</div>|</tr>|</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    return re.sub(r"\r\n|\r", "\n", text).strip()


def _pdf_attachments_text(msg: email.message.Message) -> list[str]:
    import pdfplumber

    texts: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue
        filename = _decode_filename(part)
        if not filename.lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True)
        if not data:
            continue
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_texts = [page.extract_text() or "" for page in pdf.pages]
        texts.append("\n".join(page_texts))
    return texts


def _latest_message(imap: imaplib.IMAP4_SSL, criteria: str) -> tuple[email.message.Message, int]:
    nums = _search(imap, criteria)
    if not nums:
        raise RuntimeError(f"找不到符合條件的信件：{criteria}")
    messages = [(_fetch_message(imap, num), num) for num in nums]
    messages = [(msg, num) for msg, num in messages if msg is not None]
    if not messages:
        raise RuntimeError(f"信件抓取失敗：{criteria}")
    messages.sort(key=lambda item: _message_datetime(item[0]))
    return messages[-1][0], len(nums)


# ────────────────────────────────────────────────────────────
# 金額解析
# ────────────────────────────────────────────────────────────

def _parse_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"無法解析金額：{text!r}") from exc


def _round_twd(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_aws_invoice(pdf_text: str) -> tuple[int, str]:
    """從 AWS Tax Invoice PDF 文字抓 AWS Service Charges（USD）與台幣匯率，
    回傳 (金額, 換算說明)；金額 = AWS Service Charges * 匯率 * 1.015（四捨五入到整數台幣）。
    """
    charge_match = AWS_CHARGE_RE.search(pdf_text)
    rate_match = FX_RATE_RE.search(pdf_text)
    if not charge_match:
        raise ValueError("AWS PDF 找不到 AWS Service Charges 金額")
    if not rate_match:
        raise ValueError("AWS PDF 找不到台幣匯率（1 USD = ... TWD）")
    usd_amount = _parse_decimal(charge_match.group(1))
    rate = _parse_decimal(rate_match.group(1))
    amount = _round_twd(usd_amount * rate * Decimal("1.015"))
    detail = f"TWD rate={rate}，USD{usd_amount} × rate × 1.015={amount}"
    return amount, detail


def parse_zhendan_invoice(pdf_text: str) -> tuple[int, str]:
    """從震旦行電子發票 PDF 文字抓總計金額。"""
    match = ZHENDAN_TOTAL_RE.search(pdf_text)
    if not match:
        raise ValueError("震旦行 PDF 找不到總計金額")
    return _round_twd(_parse_decimal(match.group(1))), ""


def parse_zhongdian_email(body_text: str) -> tuple[int, str]:
    """從眾點數位信件內文抓發票金額總計。"""
    match = ZHONGDIAN_TOTAL_RE.search(body_text)
    if not match:
        raise ValueError("眾點信件內文找不到「發票金額總計為」")
    return _round_twd(_parse_decimal(match.group(1))), ""


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────

def _period_window(period: str) -> tuple[datetime, datetime]:
    year, month = int(period[:4]), int(period[4:6])
    start = datetime(year, month, 1, tzinfo=TW_TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TW_TZ) if month == 12 else datetime(year, month + 1, 1, tzinfo=TW_TZ)
    return start, end


def _existing_memos(service, spreadsheet_id: str, sheet_title: str) -> set[str]:
    """讀取請款記錄／台北現有的 E 欄（費用說明），用來判斷本期是否已經新增過。"""
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!E:E",
    ).execute()
    return {str(row[0]).strip() for row in res.get("values", []) if row and str(row[0]).strip()}


def _already_submitted(existing_memos: set[str], base_memo: str) -> bool:
    """base_memo 是「YYYY.MM-標籤」；AWS 那筆實際寫入時後面會再接匯率換算說明，
    所以判斷是否重複要用「相等」或「以 base_memo＋逗號 開頭」，不能只比對完全相等。
    """
    return any(memo == base_memo or memo.startswith(base_memo + "，") for memo in existing_memos)


def submit_taipei_fixed_expenses(period: str, run_type: str = "手動") -> dict[str, Any]:
    period = (period or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202608")

    period_label = f"{period[:4]}.{period[4:6]}"
    start_dt, end_dt = _period_window(period)
    since_str = _imap_date(start_dt)
    before_str = _imap_date(end_dt)
    now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")

    spreadsheet_id, sheet_title = resolve_report_location(PAYMENT_REQUEST_TYPE, AREA)
    service = get_sheets_service()
    existing_memos = _existing_memos(service, spreadsheet_id, sheet_title)

    rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    mail_items = [
        ("Amazon Web Services", AWS_SUBJECT, "其他行銷", "彥妃信用卡", parse_aws_invoice),
        ("震旦行", ZHENDAN_SUBJECT, "其他租金", "震旦行", parse_zhendan_invoice),
        ("眾點", ZHONGDIAN_SUBJECT, "行銷費用", "眾點", parse_zhongdian_email),
    ]

    def add_mail_row(imap: imaplib.IMAP4_SSL, label: str, subject_query: str, category: str, payee: str, parse_fn) -> None:
        base_memo = f"{period_label}-{label}"
        if _already_submitted(existing_memos, base_memo):
            items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
            return
        criteria = f'(SUBJECT "{subject_query}" SINCE {since_str} BEFORE {before_str})'
        try:
            msg, matched = _latest_message(imap, criteria)
            if label == "眾點":
                amount, detail = parse_fn(_plain_body(msg))
            else:
                pdf_texts = _pdf_attachments_text(msg)
                if not pdf_texts:
                    raise RuntimeError("信件沒有 PDF 附件")
                result = None
                last_exc: Exception | None = None
                for text in pdf_texts:
                    try:
                        result = parse_fn(text)
                        break
                    except ValueError as exc:
                        last_exc = exc
                if result is None:
                    raise last_exc or RuntimeError("PDF 附件解析失敗")
                amount, detail = result
            memo = base_memo + (f"，{detail}" if detail else "")
            rows.append(["待付款", "", now_text, category, memo, amount, "", payee, ""])
            items.append({"label": label, "amount": amount, "matched": matched, "status": "成功"})
        except Exception as exc:
            errors.append(f"{label}：{exc}")
            items.append({"label": label, "amount": None, "matched": 0, "status": f"失敗：{exc}"})

    # 抓信失敗（例如信箱密碼還沒設定）不該擋掉下面兩筆不用查信的固定金額，
    # 所以連線本身的例外也要接住，只記錄失敗、不中斷主流程。當所有查信項目
    # 本期都已經新增過時，直接跳過連線，不用白白登入一次信箱。
    mailbox_user = ""
    pending_mail_items = []
    for item in mail_items:
        label = item[0]
        if _already_submitted(existing_memos, f"{period_label}-{label}"):
            items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
        else:
            pending_mail_items.append(item)
    if pending_mail_items:
        try:
            imap, mailbox_user = _imap_connect()
        except Exception as exc:
            for label, *_rest in pending_mail_items:
                errors.append(f"{label}：{exc}")
                items.append({"label": label, "amount": None, "matched": 0, "status": f"失敗：{exc}"})
        else:
            try:
                imap.select("INBOX")
                for label, subject_query, category, payee, parse_fn in pending_mail_items:
                    add_mail_row(imap, label, subject_query, category, payee, parse_fn)
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass

    # 固定金額，不查信
    fixed_items = [
        ("辦公室租金", "辦公室租金", 77343, "信義路四段房東韓承艗"),
        ("辦公室管理費", "辦公室租金", 9392, "辦公室管理費新"),
    ]
    for label, category, amount, payee in fixed_items:
        base_memo = f"{period_label}-{label}"
        if _already_submitted(existing_memos, base_memo):
            items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
            continue
        rows.append(["待付款", "", now_text, category, base_memo, amount, "", payee, ""])
        items.append({"label": label, "amount": amount, "matched": None, "status": "成功"})

    if rows:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_title}'!{REQUEST_SHEET_RANGE}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    status = "成功" if not errors else "部分失敗"
    item_lines = "\n".join(
        f"{item['label']}：{item['status']}" + (f"（{item['amount']}）" if item["amount"] is not None else "")
        for item in items
    )
    message = (
        f"{run_type}｜信箱={mailbox_user}｜期別={period_label}｜新增 {len(rows)} 筆\n{item_lines}"
    )
    log_execution(TOOL_NAME, AREA, status, message)

    return {
        "period": period,
        "period_label": period_label,
        "sheet_title": sheet_title,
        "rows_added": len(rows),
        "items": items,
        "errors": errors,
        "mailbox": mailbox_user,
    }


# ────────────────────────────────────────────────────────────
# CLI（手動測試用；主要介面在 toolapp.py 主控台）
# ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="新增『台北固定費用請款』")
    parser.add_argument("--period", required=True, help="期別，格式：YYYYMM，例如 202608")
    parser.add_argument("--run-type", default="手動")
    args = parser.parse_args()
    result = submit_taipei_fixed_expenses(args.period, args.run_type)
    print(result)


if __name__ == "__main__":
    main()
