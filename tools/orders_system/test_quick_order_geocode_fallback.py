import unittest
from unittest.mock import patch
from contextlib import ExitStack

import quick_order as q


class GeocodeFallbackTest(unittest.TestCase):
    def run_lookup(self, area, expected):
        with ExitStack() as stack:
            for name, value in {
                '_configure_environment': 'https://example.invalid',
                '_get_booking_token_for_payway': 'token',
                'geocode_address': (None, None),
            }.items():
                stack.enter_context(patch.object(q, name, return_value=value))
            check = stack.enter_context(patch.object(q, 'check_contain', return_value={'area': area}))
            # Stop after address validation, before any order operation.
            stack.enter_context(patch.object(q, 'first_nonzero', side_effect=RuntimeError('address accepted')))
            with self.assertRaisesRegex(Exception, expected):
                q.quick_create_order(
                    'test', 'ATM', '',
                    {'session': object(), 'phone': '', 'member_payload': {'member': {'member_id': '1'}}},
                    '新北市汐止區建成路142巷45弄5號2樓', '1',
                    '2026-09-26', '08:30-12:30', '4',
                )
            check.assert_called_once()

    def test_backend_can_resolve_without_google_coordinates(self):
        self.run_lookup({'area_id': '99', 'name': '汐止區'}, 'address accepted')

    def test_missing_backend_area_still_blocks(self):
        self.run_lookup({}, '查詢地址/地區失敗')

    def test_wrong_daan_area_still_blocks(self):
        self.run_lookup({'area_id': '25'}, '後台回傳 area_id=25')


if __name__ == '__main__':
    unittest.main()
