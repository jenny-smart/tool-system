from __future__ import annotations

from typing import Any, Mapping

from .config import EICredentials


def build_login_payload(
    credentials: EICredentials,
    hidden_fields: Mapping[str, Any] | None = None,
    *,
    captcha: str | None = None,
    captcha_field: str = "capchacode",
) -> dict[str, Any]:
    payload = dict(hidden_fields or {})
    payload.update(
        {
            "userid": credentials.userid,
            "passwd": credentials.password,
        }
    )
    if captcha:
        payload[captcha_field] = captcha
    return payload


def login(client: Any, *, captcha: str | None = None, captcha_field: str = "captcha"):
    """Login through an EIInvoiceClient.

    Captcha is intentionally accepted from the caller and never solved here.
    """
    return client.login(captcha=captcha, captcha_field=captcha_field)
