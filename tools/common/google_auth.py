from __future__ import annotations

import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _oauth_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    secrets_path = os.getenv("TOOLS_APP_SECRETS_FILE", "").strip()
    if secrets_path and Path(secrets_path).expanduser().exists():
        try:
            import toml

            raw = toml.loads(
                Path(secrets_path).expanduser().read_text(encoding="utf-8")
            ).get(name, "")
            return str(raw or "").strip()
        except Exception:
            return ""
    return ""


def get_google_credentials() -> Credentials:
    client_id = _oauth_value("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _oauth_value("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = _oauth_value("GOOGLE_OAUTH_REFRESH_TOKEN")

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
