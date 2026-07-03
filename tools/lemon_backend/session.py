from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import get_base_url


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


@dataclass
class LemonBackendSession:
    base_url: str | None = None
    timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 1.2
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or get_base_url()).rstrip("/")
        self.session.headers.update(DEFAULT_HEADERS)

    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self.session.request(method, self.url(path), **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_backoff * attempt)
        raise last_error

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    @staticmethod
    def page_title(response: requests.Response) -> str:
        soup = BeautifulSoup(response.text or "", "html.parser")
        title = soup.find("title")
        return title.get_text(" ", strip=True) if title else ""

    @staticmethod
    def page_error_text(response: requests.Response, max_length: int = 300) -> str:
        soup = BeautifulSoup(response.text or "", "html.parser")
        selectors = [
            ".alert-danger",
            ".alert-error",
            ".invalid-feedback",
            ".error",
            "[role=alert]",
        ]
        messages: list[str] = []
        for selector in selectors:
            for item in soup.select(selector):
                text = item.get_text(" ", strip=True)
                if text and text not in messages:
                    messages.append(text)
        if not messages:
            body_text = soup.get_text(" ", strip=True)
            for marker in ["帳號", "密碼", "登入", "錯誤", "失敗", "invalid", "error"]:
                idx = body_text.lower().find(marker.lower())
                if idx >= 0:
                    messages.append(body_text[max(0, idx - 80): idx + 180])
                    break
        return " | ".join(messages)[:max_length]

    @staticmethod
    def looks_like_login_page(response: requests.Response) -> bool:
        text = (response.text or "").lower()
        path = urlparse(response.url or "").path.lower()
        if "/login" in path:
            return True
        has_password_input = "type=\"password\"" in text or "type='password'" in text
        has_login_form = "name=\"password\"" in text or "name='password'" in text
        return has_password_input and has_login_form

    @staticmethod
    def looks_like_authenticated_page(response: requests.Response) -> bool:
        text = (response.text or "").lower()
        path = urlparse(response.url or "").path.lower()
        if "/logout" in text:
            return True
        if "/dashboard" in path or "/purchase" in path:
            return not LemonBackendSession.looks_like_login_page(response)
        return False
