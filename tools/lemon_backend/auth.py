from __future__ import annotations

from bs4 import BeautifulSoup

from .config import BackendCredentials
from .session import LemonBackendSession


def extract_csrf_token(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    token_el = soup.select_one("input[name=_token]")
    return token_el.get("value", "").strip() if token_el else ""


def login(session: LemonBackendSession, credentials: BackendCredentials) -> bool:
    """登入檸檬後台（共用 tools/common/lemon_backend_login；程式結束時自動登出）。"""
    from tools.common.lemon_backend_login import login as backend_login

    try:
        backend_login(session.session, credentials.email, credentials.password,
                      login_url=session.url("/login"), timeout=session.timeout,
                      check_url=session.url("/purchase"))
    except RuntimeError as exc:
        raise RuntimeError(f"後台登入失敗，請確認帳密：{exc}") from exc
    return True


