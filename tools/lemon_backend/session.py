from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import get_base_url


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
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
    def looks_like_login_page(response: requests.Response) -> bool:
        text = (response.text or "").lower()
        return "login" in response.url.lower() or ("password" in text and "_token" in text)
