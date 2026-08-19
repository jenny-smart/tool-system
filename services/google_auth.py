from __future__ import annotations

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from googleapiclient.discovery import build

from tools.common.google_auth import get_google_credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_credentials() -> Credentials:
    """儲值金功能改用 OAuth（jenny@lemonclean.com.tw 的 refresh token），
    因為 service account 自己沒有 Drive 儲存空間額度，複製/建立檔案會
    直接 403 storageQuotaExceeded。"""
    return get_google_credentials()


def get_gspread_client() -> gspread.Client:
    return gspread.authorize(get_credentials())


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


def get_access_token() -> str:
    """給需要直接呼叫 Sheets export 端點（例如內勤PDF產出）的地方用的 Bearer token。"""
    credentials = get_credentials()
    credentials.refresh(Request())
    return credentials.token
