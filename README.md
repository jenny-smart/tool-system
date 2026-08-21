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

新增（ATM 對帳 / 清潔異動每日檢查排程共用的檸檬後台登入帳密）：

LEMON_EMAIL
LEMON_PASSWORD

scheduled_atm_reconcile.yml -> .github/workflows/scheduled_atm_reconcile.yml
scheduled_change_order_check.yml -> .github/workflows/scheduled_change_order_check.yml
atm_reconcile.py -> tools/orders_system/atm_reconcile.py
change_order_daily_check.py -> tools/orders_system/change_order_daily_check.py
calendar_notify.py -> tools/orders_system/calendar_notify.py

- ATM 對帳：每天台北時間 11:00、15:00 各跑一次，只處理台北／台中 ATM 工作表中
  J 欄有訂單編號、且 T 欄（對帳狀態）還空白的「新增列」，已經對過帳的原資料列
  不會被讀進待處理清單，從根本避免覆蓋。完成後會在台北 Google 日曆
  （沿用 GOOGLE_SERVICE_ACCOUNT，事件邀請 jenny@hers.com.tw，30 分鐘、
  提前 20 分/10 分跳窗提醒）建立一筆通知事件。
- 清潔異動每日檢查：每天台北時間 10:00 跑一次，掃描台北／台中／桃園／新竹
  「清潔異動」工作表：待退款列若折讓單號碼（AB）與退款時間（AC）都已填寫、
  待收款列若收款時間（M）與收款發票號碼（O）都已填寫，才視為已備妥，自動
  回填後台，成功後才把 B 欄改成已退款／已收款；條件未補齊的列列進待辦，
  寄信（NOTIFY_EMAIL/NOTIFY_PASSWORD/NOTIFY_TO）並建立一筆日曆提醒。
  桃園／新竹的清潔異動試算表設定讀自主控試算表「清潔異動設定」分頁
  （地區／試算表ID／GID／啟用），不用改程式碼即可增減地區。
