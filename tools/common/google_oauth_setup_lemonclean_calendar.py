"""
一次性小工具：用 jenny@lemonclean.com.tw 本人身分授權行事曆，
讓系統通知／清潔異動、內部付款等執行紀錄能寫進她的行事曆
（服務帳號沒有她行事曆的寫入權限，會回 404 Not Found）。

用法（與 google_oauth_setup.py 一樣，需要同資料夾的 jenny_tool-oauth.json）：
    python3 tools/common/google_oauth_setup_lemonclean_calendar.py

開瀏覽器後請務必用 jenny@lemonclean.com.tw 登入並同意存取日曆；完成後把印出的
REFRESH_TOKEN 存成 GOOGLE_CALENDAR_REFRESH_TOKEN，放在：
  - 本機 Agent 的 secrets.toml（~/lemon/.streamlit/secrets.toml 或 TOOLS_APP_SECRETS_FILE）
  - Streamlit 雲端 secrets
  - GitHub Secret（排程用）
CLIENT_ID／CLIENT_SECRET 跟現有的 GOOGLE_OAUTH_CLIENT_ID／SECRET 是同一組，不用另外存。
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "jenny_tool-oauth.json",
    scopes=SCOPES,
)

print("即將開啟瀏覽器，請務必用 jenny@lemonclean.com.tw 登入並同意存取日曆……")
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n=== Calendar OAuth 完成（jenny@lemonclean.com.tw）===")
print("REFRESH_TOKEN（請存成 GOOGLE_CALENDAR_REFRESH_TOKEN）：")
print(creds.refresh_token)
