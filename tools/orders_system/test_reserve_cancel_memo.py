import unittest

import reserve_cancel as rc


class ReserveCancelMemoTest(unittest.TestCase):
    def test_already_cancelled_reserve_is_not_listed(self):
        self.assertFalse(rc._memo_matches_filter("取消系統保留單", "僅系統保留單"))
        self.assertFalse(rc._memo_matches_filter("取消系統保留單", "系統保留單或空白"))
        self.assertTrue(rc._memo_matches_filter("取消系統保留單", "全部（僅供查看）"))

    def test_active_reserve_and_blank_are_listed(self):
        self.assertTrue(rc._memo_matches_filter("系統保留單", "僅系統保留單"))
        self.assertTrue(rc._memo_matches_filter("", "系統保留單或空白"))


if __name__ == "__main__":
    unittest.main()
