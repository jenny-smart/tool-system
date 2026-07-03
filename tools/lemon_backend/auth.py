from __future__ import annotations

from bs4 import BeautifulSoup

from .config import BackendCredentials
from .session import LemonBackendSession


def extract_csrf_token(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    token_el = soup.select_one("input[name=_token]")
    return token_el.get("value", "").strip() if token_el else ""


def login(session: LemonBackendSession, credentials: BackendCredentials) -> bool:
    login_page = session.get("/login")
    login_page.raise_for_status()

    token = extract_csrf_token(login_page.text)
    if not token:
        raise RuntimeError("登入頁找不到 _token")

    response = session.post(
        "/login",
        data={
            "_token": token,
            "email": credentials.email,
            "password": credentials.password,
        },
    )
    response.raise_for_status()

    check = session.get("/purchase")
    check.raise_for_status()
    if session.looks_like_login_page(check):
        raise RuntimeError("後台登入失敗，請確認帳密")
    return True
