from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from tools.common.config_loader import get_service_account_info

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_credentials() -> Credentials:
    info = get_service_account_info()
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_gspread_client() -> gspread.Client:
    return gspread.authorize(get_credentials())


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
