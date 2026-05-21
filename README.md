unified_fix

這版統一 GitHub Actions 的密鑰流程：

- scheduled_daily.yml 和 performance_report.yml 都讀 GitHub Repository Secrets。
- GitHub Actions 不再讀 Streamlit secrets，也不需要 STREAMLIT_SECRETS_TOML。
- 寄信統一使用 NOTIFY_EMAIL / NOTIFY_PASSWORD / NOTIFY_TO。
- performance 城市後台帳號統一使用 TAIPEI_EMAIL/PASSWORD、TAICHUNG_EMAIL/PASSWORD、TAOYUAN_EMAIL/PASSWORD、HSINCHU_EMAIL/PASSWORD、KAOHSIUNG_EMAIL/PASSWORD。
- Streamlit secrets 只保留給 Streamlit app 本身，不參與 GitHub Actions 排程。

請覆蓋到 repo 位置：

performance_report.yml -> .github/workflows/performance_report.yml
scheduled_daily.yml -> .github/workflows/scheduled_daily.yml
performance_report.py -> tools/scheduled_daily/performance_report.py
performance_report_runner.py -> tools/scheduled_daily/performance_report_runner.py
send_daily_result.py -> tools/notify/send_daily_result.py
gitignore.txt -> .gitignore

GitHub Repository Secrets 需要確認：

GOOGLE_SERVICE_ACCOUNT
MASTER_SPREADSHEET_ID
TOOLS_APP_LOG_SPREADSHEET_ID（可不設，會用 MASTER_SPREADSHEET_ID）
DAILY_ROOT_FOLDER_ID
NOTIFY_EMAIL
NOTIFY_PASSWORD
NOTIFY_TO
TAIPEI_EMAIL
TAIPEI_PASSWORD
TAICHUNG_EMAIL
TAICHUNG_PASSWORD
TAOYUAN_EMAIL
TAOYUAN_PASSWORD
HSINCHU_EMAIL
HSINCHU_PASSWORD
KAOHSIUNG_EMAIL
KAOHSIUNG_PASSWORD
