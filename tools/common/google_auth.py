from __future__ import annotations

import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_google_credentials() -> Credentials:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

    missing = []

    if not client_id:
        missing.append("GOOGLE_OAUTH_CLIENT_ID")

    if not client_secret:
        missing.append("GOOGLE_OAUTH_CLIENT_SECRET")

    if not refresh_token:
        missing.append("GOOGLE_OAUTH_REFRESH_TOKEN")

    if missing:
        raise RuntimeError(
            "缺少 Google OAuth 設定：" + ", ".join(missing)
        )

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def get_drive_service():
    credentials = get_google_credentials()

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def get_sheets_service():
    credentials = get_google_credentials()

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )
