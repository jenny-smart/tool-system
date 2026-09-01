"""台北固定費用請款：依月份抓信、計算金額並寫入請款記錄。"""
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

from tools.bank_statement.internal_payment_registry import PAYMENT_REQUEST_TYPE, resolve_report_location
from tools.common.config_loader import get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.taipei_fixed_expense_invoice_helpers import (
    parse_zhongdian_region,
    sum_newebpay_invoices,
    sum_tradevan_invoices,
)

TW_TZ = timezone(timedelta(hours=8))
AREA = "台北"
TOOL_NAME = "台北固定費用請款"
REQUEST_SHEET_RANGE = "A:I"

AWS_SUBJECT = "Amazon Web Services Tax Invoice Available"
ZHENDAN_SUBJECT = "震旦集團電子發票加值中心通知信"
ZHONGDIAN_SUBJECT = "各分店每月發票金額確認"
TRADEVAN_SUBJECT = "台灣連線股份有限公司電子發票開立通知"
NEWEBPAY_SUBJECT = "藍新金流電子發票開立通知"
NEWEBPAY_COMPANIES = ("泳檬有限公司", "檸檬專業清潔有限公司", "竹盟有限公司")

AWS_CHARGE_RE = re.compile(r"AWS Service Charges\s*USD\s*([\d,]+\.\d{2})", re.I)
FX_RATE_RE = re.compile(r"1\s*USD\s*=\s*([\d.]+)\s*TWD", re.I)
ZHENDAN_TOTAL_RE = re.compile(r"總計\s+([\d,]+)")
_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _get_secret(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(key, "") or "").strip()
    except Exception:
        return ""


def _imap_connect() -> tuple[imaplib.IMAP4_SSL, str]:
    user = _get_secret("TAIPEI_FIXED_EXPENSE_GMAIL_USER") or _get_secret("GMAIL_401_USER") or _get_secret("GMAIL_USER")
    password = _get_secret("TAIPEI_FIXED_EXPENSE_GMAIL_APP_PASSWORD") or _get_secret("GMAIL_401_APP_PASSWORD") or _get_secret("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise EnvironmentError("需要台北固定費用 Gmail 帳號/App Password（可沿用 GMAIL_401 設定）")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password)
    return imap, user


def _imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{_IMAP_MONTHS[dt.month - 1]}-{dt.year:04d}"


def _search_by_subject(imap, subject: str, since: str, before: str) -> list[bytes]:
    imap.literal = subject.encode("utf-8")
    typ, data = imap.search("UTF-8", "SINCE", since, "BEFORE", before, "SUBJECT")
    if typ != "OK":
        raise RuntimeError(f"IMAP 搜尋失敗：{subject}")
    return data[0].split() if data and data[0] else []


def _fetch_message(imap, num: bytes):
    typ, data = imap.fetch(num, "(RFC822)")
    if typ != "OK" or not data or not data[0]:
        return None
    return email.message_from_bytes(data[0][1])


def _message_datetime(msg) -> datetime:
    parsed = email.utils.parsedate_tz(msg.get("Date", "") or "")
    return datetime.fromtimestamp(email.utils.mktime_tz(parsed), TW_TZ) if parsed else datetime.now(TW_TZ)


def _matching_messages(imap, subject: str, since: str, before: str):
    messages = [_fetch_message(imap, n) for n in _search_by_subject(imap, subject, since, before)]
    messages = [m for m in messages if m is not None]
    if not messages:
        raise RuntimeError(f"找不到符合條件的信件：{subject}")
    return sorted(messages, key=_message_datetime, reverse=True)


def _plain_body(msg) -> str:
    plain = html_body = ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html_body:
            html_body = text
    if plain.strip():
        return plain
    import html as html_module
    text = re.sub(r"</p>|</div>|</tr>|</li>|</td>|</th>|<br\s*/?>", "\n", html_body, flags=re.I)
    return html_module.unescape(re.sub(r"<[^>]+>", "", text))


def _decode_filename(part) -> str:
    raw = part.get_filename()
    if not raw:
        return ""
    return "".join(c.decode(cs or "utf-8", errors="replace") if isinstance(c, bytes) else c for c, cs in decode_header(raw))


def _pdf_texts(msg) -> list[str]:
    import pdfplumber
    result = []
    for part in msg.walk():
        if not _decode_filename(part).lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True)
        if data:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                result.append("\n".join(page.extract_text() or "" for page in pdf.pages))
    return result


def _parse_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"無法解析金額：{text}") from exc


def _round_twd(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_aws_invoice(text: str) -> tuple[int, str]:
    charge, rate = AWS_CHARGE_RE.search(text), FX_RATE_RE.search(text)
    if not charge or not rate:
        raise ValueError("AWS PDF 找不到服務費或台幣匯率")
    usd, fx = _parse_decimal(charge.group(1)), _parse_decimal(rate.group(1))
    amount = _round_twd(usd * fx * Decimal("1.015"))
    return amount, f"TWD rate={fx}，USD{usd} × rate × 1.015={amount}"


def parse_zhendan_invoice(text: str) -> tuple[int, str]:
    match = ZHENDAN_TOTAL_RE.search(text)
    if not match:
        raise ValueError("震旦行 PDF 找不到總計金額")
    return int(match.group(1).replace(",", "")), ""


def _parse_single_message(label: str, msg, parser):
    if label == "眾點":
        return parser(_plain_body(msg))
    last = None
    for text in _pdf_texts(msg):
        try:
            return parser(text)
        except Exception as exc:
            last = exc
    raise last or RuntimeError("信件沒有可解析的 PDF 附件")


def _period_window(period: str) -> tuple[datetime, datetime]:
    year, month = int(period[:4]), int(period[4:6])
    start = datetime(year, month, 1, tzinfo=TW_TZ)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=TW_TZ)
    return start, end


def _previous_period_label(period: str) -> str:
    year, month = int(period[:4]), int(period[4:6])
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year}.{month:02d}"


def _existing_memos(service, spreadsheet_id: str, sheet_title: str) -> set[str]:
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!E:E").execute()
    return {str(r[0]).strip() for r in result.get("values", []) if r and str(r[0]).strip()}


def _already_submitted(existing: set[str], memo: str) -> bool:
    return any(x == memo or x.startswith(memo + "，") for x in existing)


def submit_taipei_fixed_expenses(period: str, run_type: str = "手動") -> dict[str, Any]:
    period = (period or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202608")
    period_label = f"{period[:4]}.{period[4:6]}"
    mail_label = _previous_period_label(period)
    start, end = _period_window(period)
    since, before = _imap_date(start), _imap_date(end)
    now = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    spreadsheet_id, sheet_title = resolve_report_location(PAYMENT_REQUEST_TYPE, AREA)
    service = get_sheets_service()
    existing = _existing_memos(service, spreadsheet_id, sheet_title)
    rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    mailbox_user = ""

    def append_row(label: str, category: str, payee: str, amount: int, detail: str = "", memo_label: str | None = None):
        base = f"{mail_label}-{memo_label or label}"
        memo = base + (f"，{detail}" if detail else "")
        rows.append(["待付款", "", now, category, memo, amount, "", payee, ""])
        items.append({"label": label, "amount": amount, "matched": None, "status": "成功"})

    mail_labels = ["Amazon Web Services", "震旦行", "眾點", "台灣連線", *[f"藍新金流-{c}" for c in NEWEBPAY_COMPANIES]]
    needs_mail = any(not _already_submitted(existing, f"{mail_label}-{label}") for label in mail_labels)
    if needs_mail:
        try:
            imap, mailbox_user = _imap_connect()
            imap.select("INBOX")
            try:
                singles = [
                    ("Amazon Web Services", AWS_SUBJECT, "其他行銷", "彥妃信用卡", parse_aws_invoice),
                    ("震旦行", ZHENDAN_SUBJECT, "其他租金", "震旦行", parse_zhendan_invoice),
                    ("眾點", ZHONGDIAN_SUBJECT, "行銷費用", "眾點", lambda body: parse_zhongdian_region(body, "台北")),
                ]
                for label, subject, category, payee, parser in singles:
                    base = f"{mail_label}-{label}"
                    if _already_submitted(existing, base):
                        items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
                        continue
                    try:
                        messages = _matching_messages(imap, subject, since, before)
                        result = None
                        last = None
                        for msg in messages:
                            try:
                                result = _parse_single_message(label, msg, parser)
                                break
                            except Exception as exc:
                                last = exc
                        if result is None:
                            raise last or RuntimeError("找到的信件都解析失敗")
                        append_row(label, category, payee, result[0], result[1])
                        items[-1]["matched"] = len(messages)
                    except Exception as exc:
                        errors.append(f"{label}：{exc}")
                        items.append({"label": label, "amount": None, "matched": 0, "status": f"失敗：{exc}"})

                label = "台灣連線"
                if _already_submitted(existing, f"{mail_label}-{label}"):
                    items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
                else:
                    try:
                        messages = _matching_messages(imap, TRADEVAN_SUBJECT, since, before)
                        amount, detail = sum_tradevan_invoices(messages)
                        append_row(label, "行銷費用", "台灣連線股份有限公司", amount, detail)
                        items[-1]["matched"] = len(messages)
                    except Exception as exc:
                        errors.append(f"{label}：{exc}")
                        items.append({"label": label, "amount": None, "matched": 0, "status": f"失敗：{exc}"})

                pending_companies = [c for c in NEWEBPAY_COMPANIES if not _already_submitted(existing, f"{mail_label}-藍新金流-{c}")]
                for company in NEWEBPAY_COMPANIES:
                    if company not in pending_companies:
                        items.append({"label": f"藍新金流-{company}", "amount": None, "matched": None, "status": "略過（本期已新增過）"})
                if pending_companies:
                    try:
                        messages = _matching_messages(imap, NEWEBPAY_SUBJECT, since, before)
                        totals, matched = sum_newebpay_invoices(messages, tuple(pending_companies))
                        for company in pending_companies:
                            amount = totals[company]
                            if amount <= 0:
                                raise ValueError(f"找不到{company}的藍新金流發票")
                            append_row(f"藍新金流-{company}", "金流手續費", "藍新金流", amount, memo_label=f"藍新金流-{company}")
                            items[-1]["matched"] = matched
                    except Exception as exc:
                        errors.append(f"藍新金流：{exc}")
                        items.append({"label": "藍新金流", "amount": None, "matched": 0, "status": f"失敗：{exc}"})
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as exc:
            errors.append(f"Gmail：{exc}")
            items.append({"label": "Gmail", "amount": None, "matched": 0, "status": f"失敗：{exc}"})

    for label, category, amount, payee in [
        ("辦公室租金", "辦公室租金", 77343, "信義路四段房東韓承艗"),
        ("辦公室管理費", "辦公室租金", 9392, "辦公室管理費新"),
    ]:
        base = f"{period_label}-{label}"
        if _already_submitted(existing, base):
            items.append({"label": label, "amount": None, "matched": None, "status": "略過（本期已新增過）"})
        else:
            rows.append(["待付款", "", now, category, base, amount, "", payee, ""])
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
    item_lines = "\n".join(f"{i['label']}：{i['status']}" + (f"（{i['amount']}）" if i['amount'] is not None else "") for i in items)
    log_execution(TOOL_NAME, AREA, status, f"{run_type}｜信箱={mailbox_user}｜執行期別={period_label}｜發票內容期別={mail_label}｜新增 {len(rows)} 筆\n{item_lines}")
    return {"period": period, "period_label": period_label, "mail_period_label": mail_label, "sheet_title": sheet_title, "rows_added": len(rows), "items": items, "errors": errors, "mailbox": mailbox_user}


def main() -> None:
    parser = argparse.ArgumentParser(description="新增『台北固定費用請款』")
    parser.add_argument("--period", required=True)
    parser.add_argument("--run-type", default="手動")
    args = parser.parse_args()
    print(submit_taipei_fixed_expenses(args.period, args.run_type))


if __name__ == "__main__":
    main()
