import unittest
from unittest.mock import patch
import quick_order as q

ADDRESS = '新北市汐止區建成路142巷45弄5號2樓'


class BackendAddressTest(unittest.TestCase):
    def resolve(self, reply, items=None):
        payload = {'member': {'member_id': '1', 'memberAddressList': items or []}}
        with patch.object(q, 'geocode_address', side_effect=AssertionError('must not call Google')), \
             patch.object(q, 'check_contain', return_value=reply):
            return q.resolve_backend_booking_address(object(), payload, ADDRESS, 'token', '1')

    def test_empty_backend_reply_does_not_reject_complete_address(self):
        info, parts, reply = self.resolve({})
        self.assertEqual(info['country_id'], '23')
        self.assertFalse(info.get('area_id'))
        self.assertFalse(info.get('company_id'))

    def test_backend_region_is_authoritative(self):
        info, _, _ = self.resolve({'area': {'area_id': '25', 'company_id': '2'}})
        self.assertEqual(info['area_id'], '25')
        self.assertEqual(info['country_id'], '23')

    def test_backend_rejection_is_displayed(self):
        with self.assertRaisesRegex(Exception, '後台地址查詢回覆：不在服務範圍'):
            self.resolve({'return_code': '1001', 'description': '不在服務範圍'})

    def test_existing_address_survives_empty_lookup(self):
        info, _, _ = self.resolve({}, [{'id': 7, 'address': ADDRESS, 'areaId': 88,
                                       'companyId': 2, 'lat': '25', 'lng': '121'}])
        self.assertEqual(info['addressId'], '7')
        self.assertEqual(info['area_id'], 88)
        self.assertEqual(info['lat'], '25')

    def test_other_address_is_not_reused(self):
        info, _, _ = self.resolve({}, [{'id': 7, 'address': '台北市大安區信義路1號',
                                       'areaId': 25, 'companyId': 1}])
        self.assertFalse(info.get('addressId'))
        self.assertFalse(info.get('area_id'))

    def test_order_reaches_backend_calculation_without_coordinates_or_area(self):
        with patch.object(q, '_configure_environment', return_value='https://example.invalid'), \
             patch.object(q, '_get_booking_token_for_payway', return_value='token'), \
             patch.object(q, 'check_contain', return_value={}), \
             patch.object(q, 'geocode_address', side_effect=AssertionError('must not call Google')), \
             patch.object(q, 'calculate_hour', side_effect=RuntimeError('backend calculation')) as calc:
            with self.assertRaisesRegex(RuntimeError, 'backend calculation'):
                q.quick_create_order('dev', '信用卡', '',
                    {'session': object(), 'phone': '', 'member_payload': {'member': {'member_id': '1'}}},
                    ADDRESS, '1', '2026-09-26', '08:30-12:30', '4')
            data = calc.call_args.args[1]
            self.assertEqual(data['address'], ADDRESS)
            self.assertEqual(data['country_id'], '23')
            self.assertEqual(data['area_id'], '')
            self.assertEqual(data['lat'], '')

    def test_batch_uses_same_backend_address_flow(self):
        row = {'購買項目': '居家清潔', '電話': '', '地址': ADDRESS,
               '開始時間': '08:30', '結束時間': '12:30', '服務人時': '8'}
        with patch.object(q.orders, 'st', None), \
             patch.object(q.orders, 'map_to_system_slot', return_value={'system_slot': '08:30-12:30'}), \
             patch.object(q.orders, 'parse_service_human_hour', return_value=(2, 4)), \
             patch.object(q.orders, 'get_member', return_value={'member': {'member_id': '1'}}), \
             patch.object(q, 'check_contain', return_value={}), \
             patch.object(q, 'geocode_address', side_effect=AssertionError('must not call Google')), \
             patch.object(q.orders, 'prepare_base_order_data', side_effect=RuntimeError('prepare backend payload')) as prepare:
            with self.assertRaisesRegex(RuntimeError, 'prepare backend payload'):
                q.orders.process_one_group(object(), [(1, row)], 'token', None, '台北')
            info = prepare.call_args.args[2]
            self.assertEqual(info['country_id'], '23')
            self.assertFalse(info.get('area_id'))

    def test_backend_calculation_error_is_preserved(self):
        from unittest.mock import Mock
        response = Mock(status_code=200, url='https://example.invalid/ajax/calculate_hour')
        response.json.return_value = {'return_code': '1001', 'description': '地址不在服務範圍'}
        with self.assertRaisesRegex(Exception, '後台計算時數回覆：地址不在服務範圍'):
            q.orders._booking_backend_response(response, '計算時數')


if __name__ == '__main__':
    unittest.main()
