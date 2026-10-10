"""外場跨年度：104履歷跨 12/31 依信件年份分檔、預設日期用台北時間。"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from tools.field_management import orders, resume_system, roster_raise, schedule_stats

TZ = timezone(timedelta(hours=8))


def _mail(dt: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = resume_system.RESUME_SUBJECT
    msg["Date"] = dt.strftime("%a, %d %b %Y %H:%M:%S +0800")
    msg.set_content("姓名：測試\n0912345678")
    return msg


class _Imap:
    def select(self, *_):
        pass

    def logout(self):
        pass


def test_resume_range_across_new_year_splits_by_year(monkeypatch):
    mails = [_mail(datetime(2026, 12, 31, 23, 0)), _mail(datetime(2027, 1, 1, 9, 0))]
    appended = {}
    monkeypatch.setattr(resume_system, "load_system_config", lambda *_: {})
    monkeypatch.setattr(resume_system, "get_spreadsheet_id", lambda *_: "roster2026")
    monkeypatch.setattr(resume_system, "year_file_id", lambda fid, ym: f"roster{ym[:4]}")
    monkeypatch.setattr(resume_system, "get_sheets_service", lambda: None)
    monkeypatch.setattr(resume_system, "_imap_connect", lambda: (_Imap(), "box"))
    monkeypatch.setattr(resume_system, "_search", lambda imap, c: [0, 1])
    monkeypatch.setattr(resume_system, "_fetch_message", lambda imap, n: mails[n])
    monkeypatch.setattr(resume_system, "_ensure_sheet_exists", lambda *_: None)
    monkeypatch.setattr(resume_system, "append_values",
                        lambda s, sid, rng, rows: appended.setdefault(sid, []).extend(rows))
    monkeypatch.setattr(resume_system, "write_target_log", lambda **_: None)

    result = resume_system.fetch_resumes_range(
        "台北", datetime(2026, 12, 31, 0, 0, tzinfo=TZ), datetime(2027, 1, 1, 23, 59, tzinfo=TZ))

    assert result["count"] == 2
    assert [r[0][:10] for r in appended["roster2026"]] == ["2026/12/31"]
    assert [r[0][:10] for r in appended["roster2027"]] == ["2027/01/01"]


def test_default_dates_use_taipei_time(monkeypatch):
    # UTC 2026-12-31 16:30 = 台北 2027-01-01 00:30
    utc_now = datetime(2026, 12, 31, 16, 30, tzinfo=timezone.utc)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_now.astimezone(tz) if tz else utc_now.replace(tzinfo=None)

    for mod in (roster_raise, schedule_stats, orders):
        monkeypatch.setattr(mod, "datetime", FakeDT)
    assert roster_raise.today_ym() == "202701"
    assert schedule_stats.today_yyyymmdd() == "20270101"
    assert orders.today_yyyymmdd() == "20270101"
