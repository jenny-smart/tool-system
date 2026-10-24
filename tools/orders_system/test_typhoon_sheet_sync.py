import unittest
from unittest.mock import patch

import typhoon_sheet_sync as tss
from test_typhoon_transfer import _block


class _Worksheet:
    def __init__(self, values):
        self.values = values
        self.batches = []
        self.updates = []
        self.appended = []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def batch_update(self, data, value_input_option=None):
        self.batches.append((data, value_input_option))

    def update(self, range_name=None, values=None):
        self.updates.append((range_name, values))

    def append_row(self, values, value_input_option=None):
        self.appended.append(values)


class _Client:
    def __init__(self, worksheet):
        self.ws = worksheet

    def open_by_key(self, key):
        client = self

        class Book:
            def worksheet(self, title):
                return client.ws
        return Book()


SETTINGS = {
    "spreadsheet_id": "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0", "worksheet": "颱風",
    "order_col": "A", "orig_date_col": "F", "orig_period_col": "G",
    "new_date_col": "I", "new_period_col": "J", "start_row": "2",
}
SHEET = [
    ["訂單編號", "姓名", "", "", "", "原日期", "原時段", "服務時數", "新日期", "新時段"],
    ["LC00215208", "王璿瑋", "", "", "", "2026-10-23", "08:30-12:30", "4", "2027-03-03", "08:30-12:30"],
    ["LC00215218", "余葉菁", "", "", "", "2026-10-23", "14:00-18:00", "4", "2027/3/3", "14:00-18:00"],
    ["LC00217168", "李憶華", "", "", "", "2026-10-23", "09:00-11:00", "2", "2027-03-01", "09:00-11:00"],
    ["", "空白列"],
]


class SettingsTests(unittest.TestCase):
    def test_validate_accepts_url_and_rejects_bad_columns(self):
        settings = tss.validate_settings(SETTINGS)
        self.assertEqual(settings["spreadsheet_id"], "abc123")
        self.assertEqual(tss.column_index("AA"), 26)
        with self.assertRaises(ValueError):
            tss.validate_settings({**SETTINGS, "new_date_col": "1"})
        with self.assertRaises(ValueError):
            tss.validate_settings({**SETTINGS, "new_period_col": "I"})

    def test_save_then_load_per_account(self):
        ws = _Worksheet([tss.SETTINGS_HEADERS])
        client = _Client(ws)
        tss.save_settings("a@x", SETTINGS, client=client)
        ws.values.append(ws.appended[0])
        loaded = tss.load_settings("a@x", client=client)
        self.assertEqual((loaded["spreadsheet_id"], loaded["worksheet"], loaded["new_date_col"]), ("abc123", "颱風", "I"))
        self.assertEqual(tss.load_settings("b@x", client=client), tss.DEFAULT_SETTINGS)
        tss.save_settings("a@x", {**SETTINGS, "worksheet": "新"}, client=client)
        self.assertEqual(ws.updates[0][0], "A2")


class CompareAndApplyTests(unittest.TestCase):
    def setUp(self):
        self.ws = _Worksheet(SHEET)
        self.client = _Client(self.ws)
        current = {
            "LC00215208": ("2026-11-06", "14:00 - 18:00"),
            "LC00215218": ("2027-03-03", "14:00 - 18:00"),
        }

        def fetch(session, order_no):
            if order_no not in current:
                raise Exception(f"查無訂單：{order_no}")
            return _block(order_no, *current[order_no])

        self.patches = [
            patch.object(tss.tt, "_login", lambda *a: (None, "")),
            patch.object(tss.qo, "_fetch_purchase_block_for_order_no", fetch),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_compare_marks_pending_unchanged_failed(self):
        rows = tss.compare_sheet_orders("dev", "a", "b", SETTINGS, client=self.client)
        self.assertEqual([(r["row"], r["status"]) for r in rows][:2], [(2, "待更新"), (3, "未變動")])
        self.assertTrue(rows[2]["status"].startswith("查詢失敗"))
        self.assertEqual((rows[0]["orig_date"], rows[0]["now_date"], rows[0]["now_period"]),
                         ("2026-10-23", "2026-11-06", "14:00-18:00"))
        self.assertEqual(self.ws.batches, [])

    def test_apply_writes_only_pending_new_date_period(self):
        rows = tss.compare_sheet_orders("dev", "a", "b", SETTINGS, client=self.client)
        results = tss.apply_sheet_updates(SETTINGS, rows, client=self.client)
        self.assertEqual([r["status"] for r in results], ["已更新"])
        data, option = self.ws.batches[0]
        self.assertEqual(data, [{"range": "I2", "values": [["2026-11-06"]]}, {"range": "J2", "values": [["14:00-18:00"]]}])
        self.assertEqual(option, "USER_ENTERED")

    def test_apply_skips_cells_edited_after_compare(self):
        rows = tss.compare_sheet_orders("dev", "a", "b", SETTINGS, client=self.client)
        self.ws.values[1][8] = "2026-10-30"  # 客服比對後手動改了新日期
        results = tss.apply_sheet_updates(SETTINGS, rows, client=self.client)
        self.assertEqual(results[0]["status"], "已跳過：比對後表單已被修改")
        self.assertEqual(self.ws.batches, [])


if __name__ == "__main__":
    unittest.main()
