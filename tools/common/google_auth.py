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
    # 本機 Agent 沒有環境變數時，讀本機的 secrets.toml（與 Streamlit 雲端 secrets 同一份內容）
    explicit = os.getenv("TOOLS_APP_SECRETS_FILE", "").strip()
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates += [
        Path.home() / "lemon" / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    for secrets_path in candidates:
        if not secrets_path.exists():
            continue
        try:
            import toml

            raw = toml.loads(secrets_path.read_text(encoding="utf-8")).get(name, "")
        except Exception:
            continue
        if str(raw or "").strip():
            return str(raw).strip()
    # Streamlit 雲端：secrets 不一定轉成環境變數（例如改了 secrets 但程式沒重啟），直接讀 st.secrets
    try:
        import streamlit as st

        raw = st.secrets.get(name, "")
    except Exception:
        raw = ""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
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


_OAUTH_USABLE: bool | None = None


def preferred_credentials(scopes, service_account_info=None):
    """盡量用 Jenny（jenny@lemonclean.com.tw）本人的 OAuth（2026-11 Jenny）：她對各區財報、Drive 資料夾都有權限，
    服務帳號常常沒被分享（例如 lemon-bot 沒有桃園／新竹／高雄財報），也看不到 Jenny 建的資料夾。
    沒設定 OAuth 或 refresh token 失效時，才改用服務帳號（service_account_info＝回傳 dict 的函式或 dict）。"""
    global _OAUTH_USABLE
    try:
        creds = get_google_credentials()
    except RuntimeError:
        creds = None
    if creds is not None and _OAUTH_USABLE is not False:
        if _OAUTH_USABLE is None:
            try:
                from google.auth.transport.requests import Request

                creds.refresh(Request())
                _OAUTH_USABLE = True
            except Exception as exc:
                print(f"⚠️ Jenny OAuth 無法使用，改用服務帳號：{exc}", flush=True)
                _OAUTH_USABLE = False
        if _OAUTH_USABLE:
            return creds
    from google.oauth2 import service_account

    if service_account_info is None:
        from tools.common.config_loader import get_service_account_info as service_account_info
    info = service_account_info() if callable(service_account_info) else service_account_info
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)
