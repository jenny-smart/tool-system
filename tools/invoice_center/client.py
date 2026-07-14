from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .auth import build_login_payload
from .config import (
    ADD_INVOICE_ENDPOINT,
    EI_BASE_URL,
    INVOICE_EXPORT_ENDPOINT,
    INVOICE_LIST_ENDPOINT,
    LOGIN_ENDPOINT,
    EICredentials,
    get_area_credentials,
    normalize_area,
)
from .invoice import build_add_invoice_payload
from .models import InvoicePayload, InvoiceResult


INVOICE_NO_RE = re.compile(r"\b[A-Z]{2}\d{8}\b")


class EIInvoiceClient:
    def __init__(
        self,
        area: str,
        *,
        session: requests.Session | None = None,
        credentials: EICredentials | None = None,
        base_url: str = EI_BASE_URL,
        timeout: int = 30,
    ) -> None:
        self.area = normalize_area(area)
        self.credentials = credentials or get_area_credentials(self.area)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                )
            }
        )
        self.last_hidden_fields: dict[str, str] = {}
        self.last_tokens: dict[str, str] = {}
        self.logged_in = False

    def _url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.base_url}{endpoint}"

    @staticmethod
    def parse_hidden_inputs(html: str) -> dict[str, str]:
        soup = BeautifulSoup(html or "", "html.parser")
        fields: dict[str, str] = {}
        for item in soup.find_all("input", {"type": "hidden"}):
            name = item.get("name")
            if name:
                fields[name] = item.get("value", "")
        return fields

    @staticmethod
    def parse_form_action(html: str, fallback_url: str) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        form = soup.find("form")
        action = form.get("action") if form else ""
        return urljoin(fallback_url, action) if action else fallback_url

    @staticmethod
    def extract_tokens(hidden_fields: Mapping[str, str]) -> dict[str, str]:
        token_name = hidden_fields.get("struts.token.name", "token") or "token"
        token = hidden_fields.get(token_name, hidden_fields.get("token", ""))
        return {
            "struts.token.name": token_name,
            "token": token,
            "ctoken": hidden_fields.get("ctoken", ""),
        }

    @staticmethod
    def _looks_like_login_page(text: str) -> bool:
        compact = str(text or "").replace(" ", "")
        markers = ["帳號", "密碼", "驗證碼", "無密碼登入", "系統已登出", "請重新登入"]
        return any(marker in compact for marker in markers)

    @staticmethod
    def _looks_like_ei_app_page(text: str) -> bool:
        compact = str(text or "").replace(" ", "")
        markers = ["電子發票作業", "開立發票查詢", "發票開立", "invoicequery.jsp"]
        return any(marker in compact for marker in markers)

    def login(
        self,
        *,
        captcha: str | None = None,
        captcha_field: str = "capchacode",
    ) -> requests.Response:
        login_url = self._url(LOGIN_ENDPOINT)
        landing_url = getattr(self.credentials, "entry_url", "") or login_url
        landing = self.session.get(landing_url, timeout=self.timeout)
        if not (landing.text or "").strip() and landing_url != login_url:
            landing = self.session.get(login_url, timeout=self.timeout)
        landing.raise_for_status()
        if self._looks_like_ei_app_page(landing.text) and not self._looks_like_login_page(landing.text):
            self.logged_in = True
            return landing
        hidden = self.parse_hidden_inputs(landing.text)
        self.last_hidden_fields = hidden
        post_url = self.parse_form_action(landing.text, landing.url or login_url)

        payload = build_login_payload(
            self.credentials,
            hidden,
            captcha=captcha,
            captcha_field=captcha_field,
        )
        response = self.session.post(
            post_url,
            data=payload,
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        if self._looks_like_login_page(response.text) and not self._looks_like_ei_app_page(response.text):
            raise RuntimeError("EI 登入失敗或系統已登出，請確認 entry_url、帳密或驗證碼。")
        self.logged_in = True
        return response

    def refresh_token(self, endpoint: str = ADD_INVOICE_ENDPOINT) -> dict[str, str]:
        response = self.session.get(self._url(endpoint), timeout=self.timeout)
        response.raise_for_status()
        hidden = self.parse_hidden_inputs(response.text)
        tokens = self.extract_tokens(hidden)
        self.last_hidden_fields = hidden
        self.last_tokens = tokens
        return tokens

    def _apply_tokens(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("token") and data.get("ctoken"):
            return data
        tokens = self.last_tokens or self.refresh_token()
        patched = dict(data)
        patched["struts.token.name"] = tokens.get("struts.token.name", "token")
        patched["token"] = tokens.get("token", "")
        patched["ctoken"] = tokens.get("ctoken", "")
        return patched

    @staticmethod
    def _looks_like_expired_token(text: str) -> bool:
        lowered = (text or "").lower()
        markers = [
            "invalid token",
            "token expired",
            "invalid.token",
            "重複送出",
            "逾時",
            "登入逾時",
            "請重新登入",
        ]
        return any(marker in lowered for marker in markers)

    @staticmethod
    def extract_invoice_no(text: str, order_id: str = "") -> str:
        if order_id:
            from .query import _find_invoice_no

            return _find_invoice_no(text, order_id)
        match = INVOICE_NO_RE.search(text or "")
        return match.group(0) if match else ""

    def create_invoice(
        self,
        payload: InvoicePayload,
        *,
        dry_run: bool = True,
        retry_on_token_expired: bool = True,
    ) -> InvoiceResult:
        data = build_add_invoice_payload(payload)
        if dry_run:
            return InvoiceResult(
                success=True,
                dry_run=True,
                message="Dry-run only. EI addInvoice.action was not called.",
                payload=data,
            )

        data = self._apply_tokens(data)
        response = self.session.post(
            self._url(ADD_INVOICE_ENDPOINT),
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
        )
        if self._looks_like_login_page(response.text):
            return InvoiceResult(
                success=False,
                dry_run=False,
                message="EI session logged out.",
                payload=data,
                status_code=response.status_code,
                response_url=response.url,
                raw_text=response.text,
                error="EI 系統已登出或尚未登入，未開立發票。",
            )

        if (
            retry_on_token_expired
            and response.ok
            and self._looks_like_expired_token(response.text)
        ):
            tokens = self.refresh_token()
            data["struts.token.name"] = tokens.get("struts.token.name", "token")
            data["token"] = tokens.get("token", "")
            data["ctoken"] = tokens.get("ctoken", "")
            response = self.session.post(
                self._url(ADD_INVOICE_ENDPOINT),
                data=data,
                timeout=self.timeout,
                allow_redirects=True,
            )
            if self._looks_like_login_page(response.text):
                return InvoiceResult(
                    success=False,
                    dry_run=False,
                    message="EI session logged out.",
                    payload=data,
                    status_code=response.status_code,
                    response_url=response.url,
                    raw_text=response.text,
                    error="EI 系統已登出或尚未登入，未開立發票。",
                )

        invoice_no = self.extract_invoice_no(response.text, payload.orderid)
        success = response.ok and bool(invoice_no)
        return InvoiceResult(
            success=success,
            dry_run=False,
            message=(
                "EI addInvoice.action submitted."
                if success
                else "EI addInvoice.action did not return an invoice number."
            ),
            payload=data,
            status_code=response.status_code,
            response_url=response.url,
            invoice_no=invoice_no,
            raw_text=response.text,
            error="" if success else response.text[:500],
        )

    def query_invoice_by_order_id(
        self,
        order_id: str,
        date1: str,
        date2: str,
    ) -> list[dict[str, str]]:
        return self.query_invoices(date1, date2, order_id=order_id)

    def query_invoices(
        self,
        date1: str,
        date2: str,
        *,
        order_id: str = "",
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        from .query import parse_invoice_list_html, to_roc_date

        params = {
            "date1": to_roc_date(date1),
            "date2": to_roc_date(date2),
        }
        if order_id:
            params.update(
                {
                    "orderid": order_id,
                    "keyword": order_id,
                    "keys": order_id,
                    "q": order_id,
                }
            )
        params.update(dict(extra_params or {}))
        response = self.session.get(
            self._url(INVOICE_LIST_ENDPOINT),
            params=params,
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        if self._looks_like_login_page(response.text) and not self._looks_like_ei_app_page(response.text):
            raise RuntimeError("EI 系統已登出或尚未登入，無法查詢發票。")

        return parse_invoice_list_html(
            response.text,
            order_id=order_id,
            base_url=self.base_url,
        )

    def export_invoices(self) -> requests.Response:
        response = self.session.get(
            self._url(INVOICE_EXPORT_ENDPOINT),
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response
