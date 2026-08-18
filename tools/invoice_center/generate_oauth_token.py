from __future__ import annotations

"""
一次性小工具：用「電腦版應用程式」OAuth 用戶端跑一次 Google 授權流程，
產生鯨躍發票歸檔（Google Drive）要用的 GOOGLE_OAUTH_* 憑證。

用法：
    python3 -m tools.invoice_center.generate_oauth_token /path/to/client_secret.json

執行後會開瀏覽器，請用要授權的 Google 帳號（例如 jenny@lemonclean.com.tw）
登入並同意存取 Drive／試算表；完成後終端機會印出三個值，把它們加進
~/lemon/.streamlit/secrets.toml（跟現有 GOOGLE_SERVICE_ACCOUNT 平行放，
不用巢狀）：

    GOOGLE_OAUTH_CLIENT_ID = "..."
    GOOGLE_OAUTH_CLIENT_SECRET = "..."
    GOOGLE_OAUTH_REFRESH_TOKEN = "..."
"""

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="產生鯨躍發票歸檔用的 Google OAuth refresh token")
    parser.add_argument(
        "client_secret_json",
        type=Path,
        help="從 Google Cloud Console 下載的 client_secret_*.json 路徑（OAuth 用戶端類型須為「電腦版應用程式」）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.client_secret_json.expanduser()
    if not path.exists():
        print(f"找不到檔案：{path}", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=SCOPES)
    print("即將開啟瀏覽器，請用要授權的 Google 帳號登入並同意存取 Drive／試算表……")
    credentials = flow.run_local_server(port=0)

    client_config = json.loads(path.read_text(encoding="utf-8"))
    client_info = client_config.get("installed") or client_config.get("web") or {}
    client_id = client_info.get("client_id", credentials.client_id)
    client_secret = client_info.get("client_secret", credentials.client_secret)

    if not credentials.refresh_token:
        print(
            "\n授權成功，但這次沒有拿到 refresh token（可能是這組帳號＋這個用戶端"
            "之前已經同意過）。請到 https://myaccount.google.com/permissions 撤銷"
            "這個應用程式的存取權後，重新執行一次這支工具。",
            file=sys.stderr,
        )
        return 1

    print("\n授權成功！請把下面三行加進 ~/lemon/.streamlit/secrets.toml：\n")
    print(f'GOOGLE_OAUTH_CLIENT_ID = "{client_id}"')
    print(f'GOOGLE_OAUTH_CLIENT_SECRET = "{client_secret}"')
    print(f'GOOGLE_OAUTH_REFRESH_TOKEN = "{credentials.refresh_token}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
