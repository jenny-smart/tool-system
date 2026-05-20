from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo


TW_TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parents[2]
LOG_ROOT = BASE_DIR / "logs"

EXPECTED_JOBS = [
    ("performance_report", "業績報表"),
    ("schedule_report", "排班統計表"),
    ("staff_schedule", "專員班表"),
    ("orders_report", "當月次月訂單"),
    ("staff_info", "專員個資"),
    ("field_schedule_stats", "外場排班統計表"),
    ("field_staff_schedule", "外場專員班表"),
    ("field_orders", "外場訂單"),
    ("field_staff_profile", "外場專員個資"),
]
JOB_LABELS = dict(EXPECTED_JOBS)


def today_text() -> str:
    return datetime.now(TW_TZ).strftime("%Y%m%d")


def now_text() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def required_env(label: str, *names: str) -> str:
    value = first_env(*names)
    if not value:
        raise RuntimeError(f"缺少寄信設定：{label} ({' / '.join(names)})")
    return value


def split_recipients(raw: str) -> list[str]:
    return [
        item.strip()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    ]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def get_exit_code(log_dir: Path, job_name: str) -> str:
    exit_path = log_dir / f"{job_name}.exit"
    if not exit_path.exists():
        return "missing"
    return read_text(exit_path).strip() or "missing"


def status_for_exit(exit_code: str) -> tuple[str, str, str]:
    if exit_code == "0":
        return "成功", "#16a34a", "OK"
    if exit_code == "missing":
        return "尚無紀錄", "#64748b", "MISSING"
    return "失敗", "#dc2626", "FAILED"


def important_lines(content: str, limit: int = 8) -> list[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ["沒有 log 內容"]

    keywords = [
        "Traceback",
        "RuntimeError",
        "ModuleNotFoundError",
        "SMTPAuthenticationError",
        "HttpError",
        "ERROR",
        "Error",
        "Exception",
        "failed",
        "Failed",
        "失敗",
        "錯誤",
        "找不到",
        "缺少",
        "登入失敗",
        "略過寄信",
    ]
    matched = [line for line in lines if any(keyword in line for keyword in keywords)]
    return (matched or lines)[-limit:]


def build_status_rows(log_dir: Path) -> list[dict[str, str]]:
    rows = []

    for job_name, label in EXPECTED_JOBS:
        exit_code = get_exit_code(log_dir, job_name)
        content = read_text(log_dir / f"{job_name}.log")
        status_text, color, status_key = status_for_exit(exit_code)

        rows.append(
            {
                "job_name": job_name,
                "label": label,
                "status": status_text,
                "status_key": status_key,
                "color": color,
                "exit_code": exit_code,
                "content": content,
                "summary": " / ".join(important_lines(content, limit=2))[:500],
            }
        )

    return rows


def detect_overall_status(rows: list[dict[str, str]]) -> str:
    if any(row["status_key"] == "FAILED" for row in rows):
        return "部分失敗"
    if any(row["status_key"] == "MISSING" for row in rows):
        return "部分未執行"
    return "全部成功"


def build_plain_body(date_str: str, rows: list[dict[str, str]]) -> str:
    lines = [
        "Tools App 日排程執行結果",
        "",
        f"日期：{date_str}",
        f"通知時間：{now_text()}",
        "",
        "執行摘要：",
    ]

    for row in rows:
        lines.append(f"- {row['label']}：{row['status']} / exit={row['exit_code']}")
        if row["summary"]:
            lines.append(f"  {row['summary']}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("詳細 Log")
    lines.append("=" * 60)

    for row in rows:
        lines.append("")
        lines.append(f"【{row['label']}】{row['status']} / exit={row['exit_code']}")
        lines.append("-" * 60)
        content = row["content"] or "無 log"
        lines.append(content[-6000:])

    return "\n".join(lines)


def build_html_body(date_str: str, rows: list[dict[str, str]]) -> str:
    row_html = ""

    for row in rows:
        summary = html.escape(row["summary"] or "")
        row_html += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(row['label'])}</td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:{row['color']};font-weight:700;">{html.escape(row['status'])}</td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(row['exit_code'])}</td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#475569;font-size:13px;">{summary}</td>
        </tr>
        """

    return f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f8fafc;padding:24px;color:#0f172a;">
        <div style="max-width:920px;margin:auto;background:white;border-radius:18px;border:1px solid #e5e7eb;padding:24px;">
          <h2 style="margin-top:0;">Tools App 日排程執行結果</h2>
          <p style="color:#64748b;">日期：{html.escape(date_str)}</p>
          <p style="color:#64748b;">通知時間：{html.escape(now_text())}</p>

          <table style="width:100%;border-collapse:collapse;margin-top:20px;">
            <thead>
              <tr style="background:#f1f5f9;">
                <th style="text-align:left;padding:10px;">項目</th>
                <th style="text-align:left;padding:10px;">狀態</th>
                <th style="text-align:left;padding:10px;">Exit</th>
                <th style="text-align:left;padding:10px;">摘要</th>
              </tr>
            </thead>
            <tbody>
              {row_html}
            </tbody>
          </table>

          <p style="margin-top:24px;color:#64748b;font-size:13px;">
            詳細 log 可到 GitHub Actions 或 Tools App 的排程 Log 頁查看。
          </p>
        </div>
      </body>
    </html>
    """


def send_email(subject: str, plain_body: str, html_body: str) -> None:
    sender = required_env("寄件人", "NOTIFY_EMAIL", "REPORT_EMAIL_SENDER")
    password = required_env("寄件人 app password", "NOTIFY_PASSWORD", "REPORT_EMAIL_APP_PASSWORD")
    recipients_raw = required_env("收件人", "NOTIFY_TO", "REPORT_EMAIL_RECIPIENT")
    recipients = split_recipients(recipients_raw)

    if not recipients:
        raise RuntimeError("NOTIFY_TO / REPORT_EMAIL_RECIPIENT 沒有有效收件人")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def main() -> None:
    date_str = today_text()
    log_dir = LOG_ROOT / date_str
    log_dir.mkdir(parents=True, exist_ok=True)

    rows = build_status_rows(log_dir)
    overall = detect_overall_status(rows)
    subject = f"[Tools App] 日排程執行結果 {date_str} {overall}"

    plain_body = build_plain_body(date_str, rows)
    html_body = build_html_body(date_str, rows)
    send_email(subject, plain_body, html_body)

    print("日排程結果通知寄送完成")
    print(subject)


if __name__ == "__main__":
    main()
