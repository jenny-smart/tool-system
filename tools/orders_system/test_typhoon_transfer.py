import unittest
from unittest.mock import patch

import typhoon_transfer as tt


def _block(order_no, date_s, period, name="王小明", phone="0912345678", staff="宋芳儀(2)X陳佩琪(3)", status="已付款"):
    return {
        "order_no": order_no,
        "lines": [
            order_no, name, phone, "台北市大安區復興南路一段1號",
            "2026-10-01 10:00:00", "居家清潔",
            f"{date_s} (五)", period, staff,
            "付款方式：儲值金", f"付款狀態：{status}",
        ],
    }


class ParsingTests(unittest.TestCase):
    def test_parse_date_range(self):
        start, end = tt.parse_date_range("2027/03/01-2027/03/05")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2027-03-01", "2027-03-05"))
        with self.assertRaises(ValueError):
            tt.parse_date_range("2027/03/05-2027/03/01")

    def test_target_dates_weekdays_only(self):
        # 3/1 為補假但價格算平日，仍要排入
        days = tt.target_dates("2027-02-26", "2027-03-02", "與颱風假相同（平日／週末）", "2026-10-23")
        self.assertEqual(days, ["2027-02-26", "2027-03-01", "2027-03-02"])
        weekend = tt.target_dates("2027-02-26", "2027-03-02", "與颱風假相同（平日／週末）", "2026-10-24")
        self.assertEqual(weekend, ["2027-02-27", "2027-02-28"])

    def test_order_row_skips_cancelled_and_other_dates(self):
        row = tt._order_row_from_block(_block("LC1", "2026-10-23", "08:30 - 12:30"), "2026-10-23")
        self.assertEqual((row["period"], row["person"], row["clean_type_id"]), ("08:30-12:30", 2, "1"))
        self.assertIsNone(tt._order_row_from_block(_block("LC2", "2026-10-23", "08:30 - 12:30", status="取消訂單"), "2026-10-23"))
        self.assertIsNone(tt._order_row_from_block(_block("LC3", "2026-10-24", "08:30 - 12:30"), "2026-10-23"))

    def test_parse_change_date_page_reads_embedded_list(self):
        html = """
        <select id="date" name="date"><option value="">日期</option></select>
        <script>
            var json = '[{"date":"2027-03-01","section":"09:00-12:00","cleaner":["\\u5b8b\\u54c1"],"week":"\\u4e00"},{"date":"2027-03-01","section":"14:00-17:00","cleaner":[],"week":"\\u4e00"}]';
            section_list = $.parseJSON(json);
        </script>
        """
        options = tt.parse_change_date_page(html)
        self.assertEqual(options, {"2027-03-01": {"09:00-12:00": ["宋品"], "14:00-17:00": []}})

    def test_parse_change_date_page_without_list_fails_closed(self):
        with self.assertRaises(RuntimeError):
            tt.parse_change_date_page('<select name="date"><option value="">日期</option></select>')


class FindOrdersTests(unittest.TestCase):
    def test_paid_search_keeps_backend_top_to_bottom_order(self):
        blocks = [
            _block("LC9", "2026-10-23", "09:00 - 12:00"),
            _block("LC3", "2026-10-23", "08:30 - 12:30"),
            _block("LC5", "2026-10-23", "08:30 - 12:30", status="待付款"),
            _block("LC1", "2026-10-23", "08:30 - 12:30"),
        ]
        calls = []

        class Session:
            def get(self, url, params=None, **kwargs):
                calls.append(params)
                html = '<table><tr><td>LC3</td><td><a href="https://chat.line.biz/Uabc/chat/U3">LINE</a></td></tr></table>'
                return type("R", (), {"status_code": 200, "text": html})()

        with patch.object(tt, "_login", lambda *a: (Session(), "https://x")), \
                patch.object(tt.orders, "extract_order_cards_from_purchase_html", lambda html: blocks):
            rows, _ = tt.find_typhoon_orders("dev", "a", "b", "2026/10/23")
        self.assertEqual([r["order_no"] for r in rows], ["LC9", "LC3", "LC1"])
        self.assertEqual([r["line_url"] for r in rows], ["", "https://chat.line.biz/Uabc/chat/U3", ""])
        self.assertEqual(calls[0]["purchase_status"], "1")
        self.assertEqual((calls[0]["clean_date_s"], calls[0]["clean_date_e"]), ("2026-10-23", "2026-10-23"))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "order_no": "LC1", "period": "08:30-12:30", "phone": "0912345678",
            "name": "王小明", "person": 2, "service_date_before": "2026-10-23",
        }
        self.state = {"date": "2026-10-23", "period": "08:30 - 12:30"}
        self.posted = []

        def update(session, base_url, order_no, day, period, phone=""):
            self.posted.append((day, period))
            self.state.update(date=day, period=period)
            return True, "HTTP 200"

        self.patches = [
            patch.object(tt, "_change_date_options", lambda *a, **k: {
                "2027-03-01": {"09:00-12:00": ["甲"]},
                "2027-03-02": {"08:30-12:30": ["甲", "乙"]},
                "2027-03-03": {"09:00-12:00": ["甲", "乙"]},
            }),
            patch.object(tt.qo, "_update_order_service_date", update),
            patch.object(tt.qo, "_fetch_purchase_block_for_order_no",
                         lambda s, no: _block(no, self.state["date"], self.state["period"])),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_moves_to_first_available_date_with_same_period(self):
        result = tt.transfer_one_order(None, "", "dev", self.row, ["2027-02-27", "2027-03-01", "2027-03-02"])
        self.assertTrue(result["success"])
        self.assertEqual(result["new_date"], "2027-03-02")
        self.assertEqual(self.posted, [("2027-03-02", "08:30-12:30")])
        self.assertIn("2027-02-27 不在修改日期選單", result["attempts"])
        self.assertIn("2027-03-01 無 08:30-12:30 時段（可選：09:00-12:00）", result["attempts"])

    def test_no_change_when_range_full(self):
        result = tt.transfer_one_order(None, "", "dev", self.row, ["2027-03-01", "2027-03-03", "2027-03-04"])
        self.assertFalse(result["success"])
        self.assertEqual(self.posted, [])

    def test_backend_rejects_change_tries_next_day(self):
        def reject_first(session, base_url, order_no, day, period, phone=""):
            self.posted.append((day, period))
            if day != "2027-03-01":
                self.state.update(date=day, period=period)
            return True, "HTTP 200"

        options = {"2027-03-01": {"08:30-12:30": []}, "2027-03-02": {"08:30-12:30": []}}
        with patch.object(tt.qo, "_update_order_service_date", reject_first), \
                patch.object(tt, "_change_date_options", lambda *a, **k: options):
            result = tt.transfer_one_order(None, "", "dev", self.row, ["2027-03-01", "2027-03-02"])
        self.assertEqual(result["new_date"], "2027-03-02")
        self.assertEqual(len(self.posted), 2)


class CopyResultsTests(unittest.TestCase):
    def test_tsv_has_original_and_new_date_period(self):
        results = [
            {"order_no": "LC1", "name": "王小明", "phone": "0912345678", "service_date_before": "2026-10-23",
             "line_url": "https://chat.line.biz/Uabc/chat/U123",
             "period": "08:30-12:30", "success": True, "new_date": "2027-03-02", "message": "已移到"},
            {"order_no": "LC2", "name": "李\t大", "phone": "", "service_date_before": "2026-10-23",
             "period": "09:00-12:00", "success": False, "new_date": "", "message": "無法安排"},
        ]
        lines = tt.results_tsv(results).split("\n")
        self.assertEqual(lines[0].split("\t")[:8],
                         ["訂單編號", "姓名", "電話", "LINE@連結", "原日期", "原時段", "新日期", "新時段"])
        self.assertEqual(lines[1].split("\t")[:9],
                         ["LC1", "王小明", "0912345678", "https://chat.line.biz/Uabc/chat/U123",
                          "2026-10-23", "08:30-12:30", "2027-03-02", "08:30-12:30", "成功"])
        self.assertEqual(lines[2].split("\t")[3:9], ["", "2026-10-23", "09:00-12:00", "", "", "失敗"])


if __name__ == "__main__":
    unittest.main()
