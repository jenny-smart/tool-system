from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REFUND_TYPE,
    PAYMENT_REQUEST_TYPE,
    read_report_values,
)
from tools.memo_system.change_order import get_worksheet

AREAS = ["台北", "台中"]


def split_recipients(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]


def send_email(subject: str, body: str) -> None:
    sender = os.getenv("NOTIFY_EMAIL", "").strip()
    password = os.getenv("NOTIFY_PASSWORD", "").strip()
    recipients = split_recipients(os.getenv("NOTIFY_TO", ""))

    missing = [name for name, value in [
        ("NOTIFY_EMAIL", sender), ("NOTIFY_PASSWORD", password), ("NOTIFY_TO", os.getenv("NOTIFY_TO", ""))
    ] if not value]
    if missing:
        raise RuntimeError("缺少寄信設定：" + "、".join(missing))
    if not recipients:
        raise RuntimeError("NOTIFY_TO 沒有有效收件人")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def describe(label: str, candidates: list[dict[str, object]]) -> str:
    if not candidates:
        return f"　{label}：0 筆"
    rows = "、".join(str(item["sheet_row"]) for item in candidates)
    return f"　{label}：{len(candidates)} 筆（列號：{rows}）"


def main() -> None:
    lines: list[str] = []
    total = 0

    for area in AREAS:
        atm_candidates = pending_atm_refunds(get_worksheet(area).get_all_values())
        payment_candidates = pending_payment_requests(read_report_values(PAYMENT_REQUEST_TYPE, area))
        deposit_candidates = pending_deposit_refunds(read_report_values(DEPOSIT_REFUND_TYPE, area))

        total += len(atm_candidates) + len(payment_candidates) + len(deposit_candidates)

        lines.append(f"【{area}】")
        lines.append(describe("異動 ATM 退款", atm_candidates))
        lines.append(describe("請款記錄", payment_candidates))
        lines.append(describe("工具包押金退款", deposit_candidates))
        lines.append("")

    report = "\n".join(lines).strip()
    print(report, flush=True)

    if total == 0:
        print("沒有新增待處理資料，不寄信。", flush=True)
        return

    subject = f"【富邦銀行】待處理提醒：異動 ATM 退款／請款／工具包押金退款 共 {total} 筆"
    send_email(subject, report)
    print("已寄出提醒信。", flush=True)


if __name__ == "__main__":
    main()
