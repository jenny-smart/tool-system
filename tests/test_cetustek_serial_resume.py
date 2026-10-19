"""字軌匯入配號：第二層已登入正確帳號時直接接續，不重跑第一層登入。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

cetustek_serial = pytest.importorskip("tools.invoice_center.cetustek_serial")
ei_session = pytest.importorskip("tools.invoice_center.ei_session")


def test_ensure_ei_login_reuses_existing_second_layer(monkeypatch):
    if not hasattr(cetustek_serial, "ensure_ei_login") or not hasattr(ei_session, "prepare_download_session"):
        pytest.skip("modules replaced by stubs in this run")
    ei_page, portal = MagicMock(), MagicMock()
    credentials = SimpleNamespace(label="台中", userid="X")
    monkeypatch.setattr(cetustek_serial, "load_ei_accounts", lambda: {})
    monkeypatch.setattr(cetustek_serial, "ei_credentials_for", lambda area, accounts: credentials)
    monkeypatch.setattr(cetustek_serial, "find_invoice_pages", lambda context: (portal, ei_page))
    calls = []
    monkeypatch.setattr(ei_session, "prepare_download_session",
                        lambda *args: calls.append(args) or (portal, ei_page, True))

    assert cetustek_serial.ensure_ei_login(MagicMock(), "台中") is ei_page
    assert calls and calls[0][2] is ei_page and calls[0][4] is credentials
