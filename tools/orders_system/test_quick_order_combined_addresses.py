import unittest

from quick_order import _validate_combined_order_addresses


class CombinedOrderAddressValidationTest(unittest.TestCase):
    def test_allows_same_address_with_layout_whitespace(self):
        orders = [
            {"order_no": "LC001", "address": "台北市中山區明水路397巷7彗5號5樓之1"},
            {"order_no": "LC002", "address": "台北市中山區明水路397巷7彗5號5樓\n之1"},
        ]

        _validate_combined_order_addresses(orders)

    def test_blocks_different_addresses_and_lists_each_order(self):
        orders = [
            {"order_no": "LC002144601", "address": "台北市中山區明水路397巷7彗5號5樓之1"},
            {"order_no": "LC002144611", "address": "台北市大安區樂利路82號3樓"},
        ]

        with self.assertRaisesRegex(Exception, "服務地址不同") as raised:
            _validate_combined_order_addresses(orders)

        message = str(raised.exception)
        self.assertIn("LC002144601：台北市中山區明水路397巷7彗5號5樓之1", message)
        self.assertIn("LC002144611：台北市大安區樂利路82號3樓", message)
        self.assertIn("每行一筆", message)


if __name__ == "__main__":
    unittest.main()
