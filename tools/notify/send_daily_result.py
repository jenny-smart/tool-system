from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo


TW_TZ = ZoneInfo("Asia/Taipei")
LOG_ROOT = Path("logs")

JOB_LABELS = {
    "schedule_report": "排班統計表",
    "staff_schedule": "專員班表",
    "orders_report": "當月次月訂單",
    "staff_info": "專員個資",
    "performance_report": "業績報表",
}


def today_text() -> str:
    return datetime.now(TW_TZ).strftime("%Y%m%d")


def now_text() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少 GitHub Secret / 環境變數：{name}")
    return value


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def get_exit_code(log_dir: Path, job_name: str) -> str:
    exit_path = log_dir / f"{job_name}.exit"
    if not exit_path.exists():
        return "missing"
    return read_text(exit_path).strip() or "missing"


def build_status_rows(log_dir: Path) -> list[dict]:
    rows = []

    for job_name, label in JOB_LABELS.items():
        log_path = log_dir / f"{job_name}.log"
        exit_code = get_exit_code(log_dir, job_name)
        content = read_text(log_path)

        if exit_code == "0":
            status = "✅ 成功"
        elif exit_code == "missing":
            status = "⚪ 尚無紀錄"
        else:
            status = "❌ 失敗"

        rows.append(
            {
                "job_name": job_name,
                "label": label,
                "status": status,
                "exit_code": exit_code,
                "log_path": log_path,
                "content": content,
            }
        )

    return rows


def detect_overall_status(rows: list[dict]) -> str:
    has_failed = any(row["exit_code"] not in ("0", "missing") for row in rows)
    has_success = any(row["exit_code"] == "0" for row in rows)

    if has_failed:
        return "❌ 部分失敗"
    if has_success:
        return "✅ 已執行"
    return "⚪ 尚無紀錄"


def build_plain_body(date_str: str, rows: list[dict]) -> str:
    lines = [
        "Tools App 日排程執行結果",
        "",
        f"日期：{date_str}",
        f"時間：{now_text()}",
        "",
        "執行摘要：",
    ]

    for row in rows:
        lines.append(f"- {row['label']}：{row['status']} / exit={row['exit_code']}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("詳細 Log")
    lines.append("=" * 60)

    for row in rows:
        lines.append("")
        lines.append(f"【{row['label']}】{row['status']} / exit={row['exit_code']}")
        lines.append("-" * 60)

        content = row["content"] or "無 log"
        if len(content) > 6000:
            content = content[-6000:]

        lines.append(content)

    return "\n".join(lines)


def build_html_body(date_str: str, rows: list[dict]) -> str:
    row_html = ""

    for row in rows:
        color = "#16a34a"
        if row["exit_code"] not in ("0", "missing"):
            color = "#dc2626"
        elif row["exit_code"] == "missing":
            color = "#64748b"

        row_html += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{row['label']}</td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:{color};font-weight:700;">{row['status']}</td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{row['exit_code']}</td>
        </tr>
        """

    return f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f8fafc;padding:24px;color:#0f172a;">
        <div style="max-width:720px;margin:auto;background:white;border-radius:18px;border:1px solid #e5e7eb;padding:24px;">
          <h2 style="margin-top:0;">🧰 Tools App 日排程執行結果</h2>
          <p style="color:#64748b;">日期：{date_str}</p>
          <p style="color:#64748b;">通知時間：{now_text()}</p>

          <table style="width:100%;border-collapse:collapse;margin-top:20px;">
            <thead>
              <tr style="background:#f1f5f9;">
                <th style="text-align:left;padding:10px;">項目</th>
                <th style="text-align:left;padding:10px;">狀態</th>
                <th style="text-align:left;padding:10px;">Exit</th>
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
    sender = env_required("NOTIFY_EMAIL")
    password = env_required("NOTIFY_PASSWORD")
    recipients_raw = env_required("NOTIFY_TO")

    recipients = [
        email.strip()
        for email in recipients_raw.replace(";", ",").split(",")
        if email.strip()
    ]

    if not recipients:
        raise RuntimeError("NOTIFY_TO 沒有有效收件人")

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

    print("✅ 日排程結果通知寄送完成")
    print(subject)


if __name__ == "__main__":
    main()
