from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from tools.common.schedule_month_window import resolve_schedule_months
from tools.scheduled_daily import schedule_report
from tools.field_management import schedule_stats
from tools.service_management import service_schedule


TZ_TAIPEI = timezone(timedelta(hours=8))


def test_before_exact_start_date_keeps_current_and_next_month():
    base = datetime(2026, 10, 9, tzinfo=TZ_TAIPEI)
    assert resolve_schedule_months(base, "2026-10-10") == [(2026, 10), (2026, 11)]


def test_from_start_date_runs_through_following_february():
    base = datetime(2026, 10, 10, tzinfo=TZ_TAIPEI)
    assert resolve_schedule_months(base, "2026-10-10") == [
        (2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2),
    ]


def test_active_window_shrinks_one_month_at_a_time_with_two_month_floor():
    assert resolve_schedule_months(
        datetime(2026, 11, 1, tzinfo=TZ_TAIPEI), "2026-10-10"
    ) == [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]
    assert resolve_schedule_months(
        datetime(2026, 12, 1, tzinfo=TZ_TAIPEI), "2026-10-10"
    ) == [(2026, 12), (2027, 1), (2027, 2)]
    assert resolve_schedule_months(
        datetime(2027, 1, 1, tzinfo=TZ_TAIPEI), "2026-10-10"
    ) == [(2027, 1), (2027, 2)]
    assert resolve_schedule_months(
        datetime(2027, 2, 1, tzinfo=TZ_TAIPEI), "2026-10-10"
    ) == [(2027, 2), (2027, 3)]


def test_after_season_returns_to_current_and_next_month():
    base = datetime(2027, 3, 1, tzinfo=TZ_TAIPEI)
    assert resolve_schedule_months(base, "2026-10-10") == [(2027, 3), (2027, 4)]


def test_daily_download_uses_same_day_for_every_month(monkeypatch):
    monkeypatch.setenv("SERVICE_SCHEDULE_DEEP_CLEAN_START", "2026-10-10")
    assert schedule_report.get_schedule_months(
        datetime(2026, 10, 10, tzinfo=TZ_TAIPEI)
    )[-1] == ("2027-02", "20270210")


def test_service_window_uses_exact_configured_day(monkeypatch):
    monkeypatch.setenv("SERVICE_SCHEDULE_DEEP_CLEAN_START", "2026-10-10")
    assert service_schedule._resolve_month_window(
        datetime(2026, 10, 9, tzinfo=TZ_TAIPEI)
    ) == 2
    assert service_schedule._resolve_month_window(
        datetime(2026, 10, 10, tzinfo=TZ_TAIPEI)
    ) == 5


def test_service_headers_shift_and_unused_fifth_slot_is_cleared(monkeypatch):
    monkeypatch.setenv("SERVICE_SCHEDULE_DEEP_CLEAN_START", "2026-10-10")
    run_dt = datetime(2026, 11, 1, tzinfo=TZ_TAIPEI)
    months = service_schedule._active_month_keys(run_dt)
    assert months == ["202611", "202612", "202701", "202702"]

    sheet = MagicMock(row_count=500)
    service_schedule._update_schedule_month_headers(sheet, months)
    updates = sheet.batch_update.call_args.args[0]
    assert [item["values"][0][0] for item in updates[::2]] == [
        "11月", "12月", "1月", "2月", "",
    ]

    service_schedule._clear_unused_schedule_slots(sheet, months, set(months))
    assert sheet.batch_clear.call_args.args[0] == ["BO3:BW500", "EL3:ET500"]


def test_service_daily_report_has_five_month_slots_per_city():
    slots = service_schedule.CONFIG["report_month_slots"]
    assert service_schedule.CONFIG["schedule_import_slots"][0] == {
        "taipei": "G3:O",
        "taichung": "CD3:CL",
    }
    assert slots[0]["taipei_target_col"] == 11  # K 欄永遠是台北當月
    assert slots[0]["taichung_target_col"] == 101  # CW 欄永遠是台中當月
    assert [slot["taipei_source"] for slot in slots] == [
        "Q5:S5", "AF5:AH5", "AU5:AW5", "BJ5:BL5", "BY5:CA5",
    ]
    assert [slot["taichung_source"] for slot in slots] == [
        "CN5:CP5", "DC5:DE5", "DR5:DT5", "EG5:EI5", "EV5:EX5",
    ]


def test_field_target_sheet_follows_file_year():
    assert schedule_stats.target_sheet_name("20270210") == "2027排班統計表"


def test_field_annual_file_switch_finds_next_year_sibling():
    cfg = {
        "roster_default_year": 2026,
        "spreadsheet_ids": {"roster": {"台北": "file-2026"}},
    }
    drive = MagicMock()
    drive.files.return_value.get.return_value.execute.return_value = {
        "id": "file-2026",
        "name": "2026外場排程-台北",
        "parents": ["folder"],
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    drive.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "file-2027", "name": "2027外場排程-台北"}],
    }
    assert schedule_stats.get_yearly_roster_spreadsheet_id(
        cfg, "台北", 2027, drive,
    ) == "file-2027"


def test_field_explicit_annual_file_id_takes_priority():
    cfg = {
        "spreadsheet_ids": {
            "roster": {"台北": "legacy"},
            "roster_by_year": {"2027": {"台北": "configured-2027"}},
        },
    }
    drive = MagicMock()
    assert schedule_stats.get_yearly_roster_spreadsheet_id(
        cfg, "台北", 2027, drive,
    ) == "configured-2027"
    drive.files.assert_not_called()
