import unittest
from unittest import mock

import tools.orders_system  # noqa: F401  加入 sys.path 並掛上 Agent 憑證後備
import orders
import year_roots


class OrdersSystemAgentCredentialsTest(unittest.TestCase):
    def setUp(self):
        self._original = orders.get_service_account_info

    def tearDown(self):
        orders.get_service_account_info = self._original

    def test_year_roots_falls_back_to_host_service_account(self):
        orders.get_service_account_info = lambda: (_ for _ in ()).throw(FileNotFoundError("找不到 Google 憑證"))
        seen = {}

        def plain():
            seen["info"] = orders.get_service_account_info()
            return "roots"

        with mock.patch("tools.common.config_loader.get_service_account_info", return_value={"type": "sa"}), \
                mock.patch.object(tools.orders_system, "_plain_get_year_roots", plain):
            self.assertEqual(year_roots.get_year_roots(), "roots")
        self.assertEqual(seen["info"], {"type": "sa"})

    def test_keeps_orders_credentials_when_available(self):
        orders.get_service_account_info = lambda: {"type": "orders"}
        tools.orders_system._patch_orders_credentials()
        tools.orders_system._patch_orders_credentials()
        with mock.patch("tools.common.config_loader.get_service_account_info", return_value={"type": "sa"}):
            self.assertEqual(orders.get_service_account_info(), {"type": "orders"})


if __name__ == "__main__":
    unittest.main()
