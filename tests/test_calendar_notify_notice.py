"""排程行事曆：設定 SCHEDULE_NOTICE_CALENDAR_ID 後，hers／lemonclean 的事件改建在排程行事曆。"""
import unittest
from unittest.mock import patch

from tools.orders_system import calendar_notify as cn


class NoticeCalendarTests(unittest.TestCase):
    def _for(self, calendar_id, env):
        seen = []
        with patch.dict("os.environ", env, clear=False), \
             patch.object(cn, "_calendar_service", side_effect=lambda cid="": seen.append(cid) or "svc"):
            _svc, actual = cn.calendar_for(calendar_id)
        return actual, seen

    def test_hers_and_lemonclean_redirect_to_notice(self):
        env = {"SCHEDULE_NOTICE_CALENDAR_ID": "notice@group.calendar.google.com"}
        for cid in ["jenny@hers.com.tw", "jenny@lemonclean.com.tw", "", None]:
            self.assertEqual(self._for(cid, env), ("notice@group.calendar.google.com", ["notice@group.calendar.google.com"]))

    def test_other_calendars_untouched(self):
        env = {"SCHEDULE_NOTICE_CALENDAR_ID": "notice@x"}
        self.assertEqual(self._for("lemonclean925@gmail.com", env)[0], "lemonclean925@gmail.com")

    def test_without_notice_uses_default_notice_calendar(self):
        from tools.orders_system import calendar_notify as cn

        env = {"SCHEDULE_NOTICE_CALENDAR_ID": "", "GOOGLE_HERS_CALENDAR_REFRESH_TOKEN": "t"}
        with patch("tools.common.google_auth._oauth_value", side_effect=lambda k: {"GOOGLE_HERS_CALENDAR_REFRESH_TOKEN": "t"}.get(k, "")):
            self.assertEqual(self._for("jenny@hers.com.tw", env)[0], cn.DEFAULT_NOTICE_CALENDAR_ID)

if __name__ == "__main__":
    unittest.main()
