import streamlit as st
import requests
from bs4 import BeautifulSoup

LOGIN_URL = "https://backend.lemonclean.com.tw/login"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

CITY_KEY_MAP = {
    "台北": "taipei",
    "台中": "taichung",
    "桃園": "taoyuan",
    "新竹": "hsinchu",
    "高雄": "kaohsiung",
}


def get_account(city: str):
    key = CITY_KEY_MAP.get(city)

    if not key:
        raise ValueError(f"未知城市: {city}")

    return {
        "email": st.secrets["accounts"][key]["email"],
        "password": st.secrets["accounts"][key]["password"],
    }


def login_backend(email: str, password: str) -> requests.Session:
    """登入檸檬後台（共用 tools/common/lemon_backend_login；程式結束時自動登出）。"""
    from tools.common.lemon_backend_login import login as backend_login

    session = requests.Session()
    backend_login(session, email, password, login_url=LOGIN_URL, headers=HEADERS)
    return session


def load_accounts_dict():
    accounts = {}

    for city in ["台北", "台中", "桃園", "新竹", "高雄"]:
        try:
            accounts[city] = get_account(city)
        except Exception:
            pass

    return accounts
