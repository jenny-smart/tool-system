"""
一次性小工具：用 jenny@hers.com.tw 本人身分跑一次 Google 授權流程，
專門給富邦異動待處理提醒排程建立日曆事件用。

跟 google_oauth_setup.py（jenny@lemonclean.com.tw，Drive／試算表）分開，
是因為 jenny@hers.com.tw 所屬的 Google Workspace 不允許把日曆的「變更
活動」權限共用給網域外帳號，沒辦法讓 lemonclean 那組身分直接寫入她的
日曆，只能改用她本人的 OAuth 身分。

用法：
    python3 tools/common/google_oauth_setup_calendar.py

執行後會開瀏覽器，請務必用 jenny@hers.com.tw 登入並同意存取日曆；完成後
把印出的 REFRESH_TOKEN 存成 GitHub Secret：GOOGLE_HERS_CALENDAR_REFRESH_TOKEN
（CLIENT_ID／CLIENT_SECRET 跟現有的 GOOGLE_OAUTH_CLIENT_ID／SECRET 是同一個
OAuth 用戶端，不用另外存）。
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "jenny_tool-oauth.json",
    scopes=SCOPES,
)

print("即將開啟瀏覽器，請務必用 jenny@hers.com.tw 登入並同意存取日曆……")
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n=== Calendar OAuth 完成（jenny@hers.com.tw）===")
print("CLIENT_ID（應該跟現有 GOOGLE_OAUTH_CLIENT_ID 相同）：")
print(creds.client_id)

print("\nCLIENT_SECRET（應該跟現有 GOOGLE_OAUTH_CLIENT_SECRET 相同）：")
print(creds.client_secret)

print("\nREFRESH_TOKEN（請存成新的 GitHub Secret：GOOGLE_HERS_CALENDAR_REFRESH_TOKEN）：")
print(creds.refresh_token)
