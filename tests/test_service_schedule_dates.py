from datetime import datetime, timezone
from unittest.mock import MagicMock

from tools.service_management import service_schedule as schedule


def test_schedule_reads_second_page_and_uses_taipei_day(monkeypatch):
    drive = MagicMock()
    files = [
        {"id": city, "name": f"20260915排班統計表-{city}",
         "mimeType": "application/vnd.google-apps.spreadsheet"}
        for city in ("台北", "台中")
    ]
    def list_files(**kwargs):
        assert "nextPageToken" in kwargs["fields"]
        result = MagicMock()
        result.execute.return_value = (
            {"files": files} if kwargs.get("pageToken") == "page2"
            else {"files": [], "nextPageToken": "page2"}
        )
        return result
    drive.files.return_value.list.side_effect = list_files
    monkeypatch.setattr(schedule, "_drive", lambda: drive)
    monkeypatch.setattr(schedule, "_resolve_month_window", lambda _: 1)
    found = schedule.find_schedule_files(datetime(2026, 9, 14, 19, 38, tzinfo=timezone.utc))
    assert set(found["202609"]) == {"taipei", "taichung"}
    assert drive.files.return_value.list.call_count == 2


def test_mail_uses_selected_run_date_previous_day(monkeypatch):
    monkeypatch.setattr(schedule, "checkin_both", lambda *a: None)
    monkeypatch.setattr(schedule, "_find_date_row", lambda *a: 10)
    pick_mail = MagicMock(return_value="mail")
    monkeypatch.setattr(schedule, "_pick_mail", pick_mail)
    monkeypatch.setattr(schedule, "_parse_mail", lambda *a: {
        "daily_value": 1, "total_count": 2, "total_amount": 3,
    })
    schedule.step3_import_revenue(
        MagicMock(), "test", datetime(2026, 9, 14, 19, 38, tzinfo=timezone.utc)
    )
    assert pick_mail.call_count == 2
    for call in pick_mail.call_args_list:
        assert call.args[1].isoformat() == "2026-09-14T03:38:00+08:00"
