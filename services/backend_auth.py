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
    session = requests.Session()

    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")

    token_input = soup.find("input", {"name": "_token"})

    if not token_input:
        raise RuntimeError("找不到 _token")

    payload = {
        "_token": token_input.get("value"),
        "email": email,
        "password": password,
    }

    login_res = session.post(
        LOGIN_URL,
        data=payload,
        headers=HEADERS,
        allow_redirects=True,
    )

    login_res.raise_for_status()

    if "login" in login_res.url.lower():
        raise RuntimeError("登入失敗")

    return session

def load_accounts_dict():
    accounts = {}

    for city in ["台北", "台中", "桃園", "新竹", "高雄"]:
        try:
            accounts[city] = get_account(city)
        except Exception:
            pass

    return accounts
