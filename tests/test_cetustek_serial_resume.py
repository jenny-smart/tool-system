"""字軌匯入配號：接續目前畫面，交給共用的 ei_session.open_ei_page。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

cetustek_serial = pytest.importorskip("tools.invoice_center.cetustek_serial")
ei_session = pytest.importorskip("tools.invoice_center.ei_session")


def _real():
    if not hasattr(cetustek_serial, "ensure_ei_login") or not hasattr(ei_session, "prepare_download_session"):
        pytest.skip("modules replaced by stubs in this run")


def test_ensure_ei_login_uses_shared_open_ei_page(monkeypatch):
    _real()
    page = MagicMock()
    calls = []
    monkeypatch.setattr(cetustek_serial, "load_ei_accounts", lambda: {"a": 1})
    monkeypatch.setattr(ei_session, "open_ei_page", lambda *args: calls.append(args) or (page, True))
    assert cetustek_serial.ensure_ei_login(MagicMock(), "台中") is page
    assert calls[0][1:] == ("台中", {"a": 1})


def test_open_ei_page_reuses_existing_second_layer(monkeypatch):
    _real()
    ei_page, portal = MagicMock(), MagicMock()
    credentials = SimpleNamespace(label="台中", userid="X")
    import tools.invoice_center.chrome_cdp as chrome_cdp
    import tools.invoice_center.ei_export_all as ei_export_all
    monkeypatch.setattr(ei_export_all, "credentials_for", lambda area, accounts: credentials)
    monkeypatch.setattr(chrome_cdp, "find_invoice_pages", lambda context: (portal, ei_page))
    monkeypatch.setattr(ei_session, "ensure_expected_ei_login", lambda page, cred: True)
    login = MagicMock()
    monkeypatch.setattr(ei_session, "login_portal", login)
    assert ei_session.open_ei_page(MagicMock(), "台中", {}) == (ei_page, True)
    login.assert_not_called()
