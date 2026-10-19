import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from tools import local_agent_queue as queue
from tools.scheduled_daily import fubon_statement_queue as fsq

TZ = ZoneInfo("Asia/Taipei")


def test_morning_always_other_slots_only_workdays(monkeypatch):
    monkeypatch.setattr("tools.common.tw_holidays.is_workday", lambda day, **kw: day.weekday() < 5)
    assert fsq.should_queue(datetime(2026, 10, 24, 8, 0, tzinfo=TZ))        # 週六 08:00
    assert not fsq.should_queue(datetime(2026, 10, 24, 11, 0, tzinfo=TZ))   # 週六 11:00
    assert not fsq.should_queue(datetime(2026, 10, 25, 16, 30, tzinfo=TZ))  # 週日 16:30
    assert fsq.should_queue(datetime(2026, 10, 23, 16, 30, tzinfo=TZ))      # 週五 16:30


def test_national_holiday_skips_afternoon(monkeypatch):
    monkeypatch.setattr("tools.common.tw_holidays.is_workday", lambda day, **kw: day != date(2026, 10, 9))
    assert not fsq.should_queue(datetime(2026, 10, 9, 11, 0, tzinfo=TZ))


def _task(row, action, area, day, task_id):
    return {"_row": str(row), "task_id": task_id, "action": action, "status": "pending",
            "params_json": json.dumps({"area": area, "start_date": day, "end_date": day})}


def test_only_newest_same_day_download_runs(monkeypatch):
    updates = []
    monkeypatch.setattr(queue, "update_task", lambda row, fields, **kw: updates.append((row, fields["status"])))
    pending = [
        _task(2, "fubon.download", "台北", "2026-10-19", "a"),
        _task(3, "fubon.download", "台中", "2026-10-19", "b"),
        _task(4, "fubon.download", "台北", "2026-10-18", "c"),
        _task(5, "cetustek.login", "台北", "2026-10-19", "d"),
        _task(6, "fubon.download", "台北", "2026-10-19", "e"),
    ]
    kept = queue._skip_superseded_downloads(pending, service=None, spreadsheet_id="")
    assert [t["task_id"] for t in kept] == ["b", "c", "d", "e"]
    assert updates == [(2, "cancelled")]
