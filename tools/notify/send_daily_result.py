# tools/notify/send_daily_result.py

from pathlib import Path
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =========================================================
# 設定
# =========================================================

LOG_DIR = Path("logs")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

SENDER_EMAIL = "你的gmail@gmail.com"
SENDER_PASSWORD = "你的 Gmail App Password"

RECEIVER_EMAILS = [
    "你的信箱@gmail.com",
]

# =========================================================
# 取得今日 log
# =========================================================

today = datetime.now().strftime("%Y%m%d")
today_log_dir = LOG_DIR / today

if not today_log_dir.exists():
    raise RuntimeError(f"找不到今日 log 資料夾：{today_log_dir}")

# =========================================================
# 讀取所有 log
# =========================================================

log_contents = []

for log_file in sorted(today_log_dir.glob("*.log")):
    try:
        content = log_file.read_text(encoding="utf-8")
    except Exception:
        content = "讀取失敗"

    log_contents.append(
        f"""
==================================================
檔案：{log_file.name}
==================================================

{content}

"""
    )

full_log = "\n".join(log_contents)

# =========================================================
# 判斷成功 / 失敗
# =========================================================

if "Traceback" in full_log or "❌" in full_log:
    status = "❌ 部分失敗"
else:
    status = "✅ 全部成功"

# =========================================================
# Email 內容
# =========================================================

subject = f"[Tools App] 日排程執行結果 {today} {status}"

body = f"""
Tools App 日排程執行結果

日期：
{today}

狀態：
{status}

--------------------------------------------------

{full_log}
"""

# =========================================================
# 寄送 Email
# =========================================================

msg = MIMEMultipart()
msg["From"] = SENDER_EMAIL
msg["To"] = ", ".join(RECEIVER_EMAILS)
msg["Subject"] = subject

msg.attach(MIMEText(body, "plain", "utf-8"))

server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
server.starttls()

server.login(SENDER_EMAIL, SENDER_PASSWORD)

server.sendmail(
    SENDER_EMAIL,
    RECEIVER_EMAILS,
    msg.as_string(),
)

server.quit()

print("✅ 日排程結果通知寄送完成")
