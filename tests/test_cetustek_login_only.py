from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


playwright_module = types.ModuleType("playwright")
playwright_sync_module = types.ModuleType("playwright.sync_api")
playwright_sync_module.sync_playwright = MagicMock()
playwright_module.sync_api = playwright_sync_module
sys.modules.setdefault("playwright", playwright_module)
sys.modules.setdefault("playwright.sync_api", playwright_sync_module)

paste_module = types.ModuleType("tools.invoice_center.cetustek_invoice_paste")
paste_module.INVOICE_CREATE_URL = "https://www.ei.com.tw/InvoiceRent/invoiceadd.jsp"
paste_module._is_invoice_create_page = MagicMock()
paste_module.process_pending_invoice_payloads = MagicMock()
sys.modules.setdefault("tools.invoice_center.cetustek_invoice_paste", paste_module)

export_module = types.ModuleType("tools.invoice_center.ei_export_all")
for name in (
    "configured_areas",
    "credentials_for",
    "load_accounts",
    "login_second",
    "login_portal",
    "logout_second",
    "open_second_login",
    "portal_values",
):
    setattr(export_module, name, MagicMock())
export_module.EI_HOME_URL = "https://www.ei.com.tw/InvoiceRent/welcome.jsp"
export_module.EI_LOGIN_URL = "https://www.ei.com.tw/InvoiceRent/index.jsp"
sys.modules.setdefault("tools.invoice_center.ei_export_all", export_module)

chrome_module = types.ModuleType("tools.invoice_center.chrome_cdp")
chrome_module.DEFAULT_CDP_URL = "http://127.0.0.1:9222"
chrome_module.connect_existing_chrome = MagicMock()
chrome_module.find_invoice_pages = MagicMock()
sys.modules.setdefault("tools.invoice_center.chrome_cdp", chrome_module)

from tools.invoice_center import cetustek_login_only as login


class EISessionProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        login._is_invoice_create_page.reset_mock()

    def test_valid_session_probe_does_not_navigate_existing_page(self) -> None:
        page = MagicMock()
        probe = MagicMock()
        page.context.new_page.return_value = probe
        login._is_invoice_create_page.return_value = True

        self.assertTrue(login._ei_logged_in(page))

        page.goto.assert_not_called()
        probe.goto.assert_called_once_with(
            login.INVOICE_CREATE_URL,
            wait_until="domcontentloaded",
            timeout=15000,
        )
        probe.close.assert_called_once()

    def test_invalid_session_is_not_reused(self) -> None:
        page = MagicMock()
        probe = MagicMock()
        page.context.new_page.return_value = probe
        login._is_invoice_create_page.return_value = False

        self.assertFalse(login._ei_logged_in(page))
        page.goto.assert_not_called()
        probe.close.assert_called_once()

    def test_session_reused_only_when_virtual_id_matches(self) -> None:
        page = MagicMock()
        probe = MagicMock()
        page.context.new_page.return_value = probe
        probe.locator.return_value.inner_text.return_value = (
            "虛實合一： 52551362 寧盟 您好"
        )
        login._is_invoice_create_page.return_value = True

        self.assertTrue(login._ei_logged_in(page, "52551362"))
        self.assertFalse(login._ei_logged_in(page, "42627791"))

    def test_wrong_logged_in_account_is_logged_out_and_reverified(self) -> None:
        page = MagicMock()
        credentials = MagicMock(userid="42627791", label="台北")

        with patch.object(
            login, "_ei_logged_in", side_effect=[False, True, True]
        ), patch.object(login, "logout_second") as logout, patch.object(
            login, "login_second"
        ) as second_login:
            reused = login.ensure_expected_ei_login(page, credentials)

        self.assertFalse(reused)
        logout.assert_called_once_with(page, "台北")
        page.goto.assert_called_once_with(
            login.EI_LOGIN_URL, wait_until="domcontentloaded"
        )
        second_login.assert_called_once_with(page, credentials)

    def test_wrong_account_after_login_is_blocked(self) -> None:
        page = MagicMock()
        credentials = MagicMock(userid="42627791", label="台北")

        with patch.object(
            login, "_ei_logged_in", side_effect=[False, False, False]
        ), patch.object(login, "login_second"):
            with self.assertRaisesRegex(RuntimeError, "禁止繼續"):
                login.ensure_expected_ei_login(page, credentials)


if __name__ == "__main__":
    unittest.main()

