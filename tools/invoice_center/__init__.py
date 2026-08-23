from __future__ import annotations

from .models import InvoiceLineItem, InvoicePayload, InvoiceResult

__all__ = [
    "EIInvoiceClient",
    "InvoiceLineItem",
    "InvoicePayload",
    "InvoiceResult",
    "create_invoice_from_payload",
    "preview_invoice_from_order",
]


def __getattr__(name: str):
    if name == "EIInvoiceClient":
        from .client import EIInvoiceClient

        return EIInvoiceClient
    if name in {"create_invoice_from_payload", "preview_invoice_from_order"}:
        from . import bridge

        return getattr(bridge, name)
    raise AttributeError(name)


# Invoice create UI extension: choose area and pending change-order rows before lookup.
from . import ui as _ui
from .pending_charge_ui import install as _install_pending_charge_ui

_install_pending_charge_ui(_ui)
