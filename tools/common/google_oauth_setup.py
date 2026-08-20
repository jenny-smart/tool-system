from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "jenny_tool-oauth.json",
    scopes=SCOPES,
)

creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n=== OAuth 完成 ===")
print("CLIENT_ID:")
print(creds.client_id)

print("\nCLIENT_SECRET:")
print(creds.client_secret)

print("\nREFRESH_TOKEN:")
print(creds.refresh_token)
