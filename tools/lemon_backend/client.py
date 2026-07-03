from __future__ import annotations

from typing import Any

from . import orders
from .auth import login as login_session
from .config import BackendCredentials, get_base_url, get_credentials, normalize_area
from .models import BackendOrder, BackendResult
from .session import LemonBackendSession


class BackendClient:
    def __init__(
        self,
        area: str,
        *,
        env_name: str | None = None,
        credentials: BackendCredentials | None = None,
        session: LemonBackendSession | None = None,
    ) -> None:
        self.area = normalize_area(area)
        self.credentials = credentials or get_credentials(self.area)
        self.session = session or LemonBackendSession(base_url=get_base_url(env_name))
        self.logged_in = False

    def login(self) -> bool:
        self.logged_in = login_session(self.session, self.credentials)
        return self.logged_in

    def ensure_login(self) -> None:
        if not self.logged_in:
            self.login()

    def search_order(self, order_no: str) -> list[BackendOrder]:
        self.ensure_login()
        return orders.search_order(self.session, order_no)

    def get_order(self, order_no: str) -> BackendOrder | None:
        self.ensure_login()
        return orders.get_order(self.session, order_no)

    def get_purchase_page(self, params: dict[str, Any] | None = None):
        self.ensure_login()
        return orders.get_purchase_page(self.session, params)

    def get_booking_page(self, payway: str):
        self.ensure_login()
        return orders.get_booking_page(self.session, payway)

    def search_orders_by_phone(self, phone: str) -> list[BackendOrder]:
        self.ensure_login()
        return orders.search_orders_by_phone(self.session, phone)

    def update_invoice_no(self, order_no: str, invoice_no: str) -> BackendResult:
        return BackendResult(
            success=False,
            message="TODO: update_invoice_no 尚未實作",
            data={"order_no": order_no, "invoice_no": invoice_no},
        )

    def update_allowance_no(
        self,
        order_no: str,
        allowance_no: str,
        amount: Any | None = None,
    ) -> BackendResult:
        return BackendResult(
            success=False,
            message="TODO: update_allowance_no 尚未實作",
            data={"order_no": order_no, "allowance_no": allowance_no, "amount": amount},
        )
