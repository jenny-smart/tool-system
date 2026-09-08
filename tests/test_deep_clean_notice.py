from datetime import datetime, timedelta, timezone

from tools.service_management.deep_clean_notice import (
    DeepCleanSettings,
    _extra_charge,
    _validate_settings,
    build_nonroutine_notice,
    build_nonroutine_notice_rows,
    build_notice_rows,
    resolve_deep_clean_sheet_ids,
)


TZ = timezone(timedelta(hours=8))


def _row(day, name="王小明", person_hrs=6, service="2人", start_hour=9, end_hour=12):
    start = datetime.fromisoformat(day).replace(hour=start_hour, tzinfo=TZ)
    return {
        "start_dt": start,
        "date_str": start.strftime("%Y/%m/%d"),
        "weekday": "一",
        "start_str": f"{start_hour:02d}:00",
        "end_str": f"{end_hour:02d}:00",
        "service": service,
        "person_hrs": person_hrs,
        "name": name,
        "phone": "0912345678",
        "address": "台北市測試路1號",
    }


def test_extra_charge_uses_weekend_rate():
    weekday = _row("2026-12-18")
    weekend = _row("2026-12-19")
    assert _extra_charge(weekday, 100, 250) == 300
    assert _extra_charge(weekend, 100, 250) == 750


def test_extra_charge_recomputes_people_times_hours_instead_of_trusting_stale_total():
    saturday_three_hours = _row(
        "2027-01-09", person_hrs=9, service="2人", start_hour=14, end_hour=17,
    )
    two_people_six_hours = _row(
        "2027-01-09", person_hrs=999, service="2人", start_hour=9, end_hour=15,
    )
    three_people_four_hours = _row(
        "2027-01-09", person_hrs=999, service="3人", start_hour=9, end_hour=13,
    )

    assert _extra_charge(saturday_three_hours, 150, 200) == 600
    assert _extra_charge(two_people_six_hours, 150, 200) == 1200
    assert _extra_charge(three_people_four_hours, 150, 200) == 1200


def test_part1_saturday_notice_uses_600_for_two_people_three_hours():
    rows = [_row("2027-01-09", person_hrs=9, service="2人", start_hour=14, end_hour=17)]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        150, 200, 200, 250,
    )

    assert output[0][12] == 600
    assert "PART 1 次數／年節加價金額：1 次／NT$600" in output[0][15]


def test_regular_notice_contains_mail_merge_fields_and_next_service():
    rows = [_row("2026-12-18"), _row("2027-01-23"), _row("2027-02-12")]
    output = build_notice_rows(
        "台北",
        rows,
        {"王小明": {"lineValue": "https://chat.line.biz/example", "email": "vip@example.com"}},
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100,
        250,
        200,
        300,
        "2026/11/03（二）17:00",
    )
    assert len(output) == 1
    row = output[0]
    assert row[1] == "定期VIP"
    assert row[4] == "vip@example.com"
    assert "2027/02/12" in row[9]
    assert row[16] == "待寄送"
    assert "如改期，將依實際服務日期重新計算" in row[15]


def test_nonroutine_notice_uses_open_window_and_correct_customer_label():
    notice = build_nonroutine_notice(
        "測試客戶",
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, tzinfo=TZ),
        100,
        250,
        200,
        300,
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
    )
    assert "《VIP 客戶年節大掃除加價收費說明》" in notice
    assert "VIP 開放預約時間：2026/11/05～2026/11/10" in notice
    assert "VIP 定期客戶" not in notice


def test_zero_rates_are_valid_while_prices_are_undecided():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        0,
        0,
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        0,
        0,
        "",
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
    )

    _validate_settings(settings)


def test_nonroutine_list_subtracts_calendar_regular_vip_by_phone():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        100,
        250,
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        200,
        300,
        "2026/11/03 17:00",
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
    )
    regular_row = ["台北", "定期VIP", "王小明", "0912345678"]
    members = [
        {"area": "台北", "member_id": "1", "name": "王小明", "phone": "0912345678", "email": "a@example.com"},
        {"area": "台北", "member_id": "2", "name": "陳小華", "phone": "0987654321", "email": "b@example.com"},
    ]

    output = build_nonroutine_notice_rows(members, [regular_row], settings)

    assert len(output) == 1
    assert output[0][2] == "陳小華"
    assert output[0][11] == "待寄送"


def test_resolve_year_files_from_master_root(monkeypatch):
    class FakeWorksheet:
        def get(self, _range):
            return [
                ["設定項目", "設定值"],
                ["大掃除根目錄資料夾 ID", "root-id"],
            ]

    class FakeSpreadsheet:
        def worksheet(self, _name):
            return FakeWorksheet()

    class FakeGspread:
        def open_by_key(self, _key):
            return FakeSpreadsheet()

    class FakeDrive:
        def __init__(self, _service):
            pass

        def find_folder(self, parent_id, name):
            assert (parent_id, name) == ("root-id", "2026")
            return {"id": "year-folder"}

        def find_google_sheet_by_name(self, folder_id, name):
            assert folder_id == "year-folder"
            return [{"id": "settings-id" if name.endswith("系統調整") else "notice-id"}]

    monkeypatch.setattr("tools.service_management.deep_clean_notice._gc", lambda: FakeGspread())
    monkeypatch.setattr("services.google_auth.get_drive_service", lambda: object())
    monkeypatch.setattr("services.google_drive.DriveService", FakeDrive)

    assert resolve_deep_clean_sheet_ids(2026, "master-id") == ("settings-id", "notice-id")
