from datetime import datetime, timedelta, timezone

from tools.service_management.deep_clean_notice import (
    DeepCleanSettings,
    _extra_charge,
    _canonical_settings_values,
    _comparison_values,
    _engineer_price_text,
    _service_intro,
    _write_engineer_system_sheet,
    _settings_from_row,
    _settings_row,
    _validate_settings,
    build_nonroutine_notice,
    build_nonroutine_notice_rows,
    build_notice_rows,
    deep_clean_settings_form_values,
    resolve_deep_clean_sheet_ids,
)


TZ = timezone(timedelta(hours=8))


def _row(
    day,
    name="王小明",
    person_hrs=6,
    service="2人",
    start_hour=9,
    end_hour=12,
    note="",
    status="",
):
    start = datetime.fromisoformat(day).replace(hour=start_hour, tzinfo=TZ)
    return {
        "start_dt": start,
        "date_str": start.strftime("%Y/%m/%d"),
        "weekday": "一",
        "start_str": f"{start_hour:02d}:00",
        "end_str": f"{end_hour:02d}:00",
        "service": service,
        "note": note,
        "status": status,
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
    rows = [_row("2026-12-07"), _row("2026-12-18"), _row("2027-01-23"), _row("2027-02-12")]
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
    assert "服務地址：台北市測試路1號" in row[15]
    assert "您原訂週期於（2026/12～2027/02）" in row[15]
    assert "2026/12/07(一)" in row[15]
    assert "提醒您目前該服務地址2026/12～2027/02的服務日期/時段如下" in row[21]
    assert "2026/12/07（週一）09:00–12:00" in row[21]
    assert "年節後第一次服務日期：2027/02/12(一)" in row[21]
    assert "請您協助確認，若需要調整，請連繫我們～" in row[21]


def test_service_reminder_hides_pending_paused_and_lunar_holiday_services():
    rows = [
        _row("2026-12-18"),
        _row("2027-01-06", status="待確認"),
        _row("2027-02-03", status="暫停"),
        _row("2027-02-06"),
        _row("2027-03-03"),
    ]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100, 250, 200, 300, "",
        datetime(2027, 2, 5, tzinfo=TZ), datetime(2027, 2, 10, 23, 59, tzinfo=TZ),
    )

    reminder = output[0][21]
    assert "2026/12/18" in reminder
    assert "2027/01/06" not in reminder
    assert "2027/02/03" not in reminder
    assert "農曆年暫停服務期間：2027/02/05-2027/02/10" in reminder
    assert "原訂服務日期：\n2027/02/06（週一）09:00–12:00 暫停一次" in reminder
    assert "年節後第一次服務日期：2027/03/03(一)" in reminder
    notice = output[0][15]
    assert "農曆年暫停服務期間：2027/02/05-2027/02/10" in notice
    assert "原訂服務日期：\n2027/02/06（週一）09:00–12:00 暫停一次" in notice
    assert notice.index("農曆年暫停服務期間") < notice.index("年節後第一次服務日期")


def test_holiday_block_omits_original_service_label_when_address_has_none():
    rows = [_row("2026-12-18"), _row("2027-03-03")]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100, 250, 200, 300, "",
        datetime(2027, 2, 5, tzinfo=TZ), datetime(2027, 2, 10, 23, 59, tzinfo=TZ),
    )

    for content in (output[0][15], output[0][21]):
        assert "農曆年暫停服務期間：2027/02/05-2027/02/10" in content
        assert "原訂服務日期" not in content


def test_service_reminder_is_blank_when_all_dates_are_pending_or_paused():
    rows = [
        _row("2026-12-18", status="待確認"),
        _row("2027-01-23", status="暫停"),
    ]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100, 250, 200, 300,
    )

    assert output[0][21] == ""


def test_regular_notice_keeps_calendar_service_note_and_status_fields():
    rows = [
        _row("2026-12-18", service="3人", note="每月確認", status="已安排"),
        _row("2027-01-23", status="暫停"),
        _row("2027-02-12", note="請先聯絡", status="保留單"),
    ]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100, 250, 200, 300,
    )

    row = output[0]
    assert "3人｜備註：每月確認｜狀態：待確認" in row[7]
    assert "狀態：暫停" in row[8]
    assert "備註：請先聯絡｜狀態：保留單" in row[9]
    assert row[18] == "2026/12/18：3人"
    assert row[19] == "2026/12/18：每月確認\n2027/02/12：請先聯絡"
    assert row[20] == (
        "2026/12/18：待確認\n2027/01/23：暫停\n2027/02/12：保留單"
    )


def test_monthly_confirmation_hides_schedule_and_asks_for_date_by_deadline():
    rows = [
        _row("2026-12-17", note="每月確認", status="已安排"),
        _row("2026-12-31", note="每月確認", status="未安排"),
        _row("2027-02-11", note="每月確認", status="已安排"),
    ]
    output = build_notice_rows(
        "台北", rows, {},
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100, 250, 200, 300,
        "2026/11/03（二）17:00",
    )

    row = output[0]
    notice = row[15]
    assert "🕓 請於 2026/11/03 前告知欲安排的日期。" in notice
    assert "服務地址：台北市測試路1號" in notice
    assert "您原訂週期於年節期間" not in notice
    assert "年節加價服務日期" not in notice
    assert "PART 1 次數／年節加價金額" not in notice
    assert "年節後第一次服務日期" not in notice
    assert "2026/12/17：待確認" in row[20]
    assert "2026/12/31：待確認" in row[20]
    assert "2027/02/11：待確認" in row[20]


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
    assert "2026年節大掃除期間：2026/12/15～2027/02/04" in notice
    assert "基本時數為 2 人 3 小時起" in notice
    assert "服務內容同居家清潔，以人時計價" in notice


def test_price_comparison_uses_matching_part_customer_type_and_day_type():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        125,
        225,
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        225,
        275,
        "",
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
        "notice-id",
        300,
        350,
        350,
        400,
    )

    assert _comparison_values(settings, 2, False) == [600, 250, 700, 450, 700, 450, 800, 550]
    assert _comparison_values(settings, 2, True) == [3000, 2650, 3100, 2850, 3100, 2850, 3200, 2950]
    engineer_text = _engineer_price_text(settings, vip=False)
    assert "PART 1 2026/12/15～2027/01/21" in engineer_text
    assert "週末每2人1小時年節加價NT$350" in engineer_text
    assert "PART 2 2027/01/22～2027/02/04" in engineer_text
    assert "週末每2人1小時年節加價NT$400" in engineer_text


def test_service_intro_uses_full_period_and_three_hour_minimum():
    intro = _service_intro(
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
    )

    assert "2026/12/15～2027/02/04" in intro
    assert "2 人 3 小時起" in intro


def test_engineer_system_sheet_contains_current_system_changes():
    class FakeSheet:
        col_count = 5

        def resize(self, cols):
            self.col_count = cols

        def clear(self):
            pass

        def update(self, values, range_name, value_input_option):
            self.values = values

        def format(self, _range, _format):
            pass

        def freeze(self, rows):
            pass

    class FakeSpreadsheet:
        def __init__(self):
            self.sheet = FakeSheet()

        def worksheet(self, _title):
            return self.sheet

    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        125, 225,
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        225, 275, "",
        datetime(2026, 11, 5, tzinfo=TZ), datetime(2026, 11, 10, tzinfo=TZ),
        "notice-id", 300, 350, 350, 400,
    )
    spreadsheet = FakeSpreadsheet()

    title = _write_engineer_system_sheet(spreadsheet, settings)

    assert title == "2026系統工作表修改資料"
    values = spreadsheet.sheet.values
    assert values[1][2] == "2026/12/15～2027/02/04"  # C2
    assert values[2][2] == "2026/12/15～2027/01/21"  # C3
    assert values[3][2] == "2027/01/22～2027/02/04"  # C4
    assert values[4][4:6] == ["125元(含稅)", "225元(含稅)"]  # E5:F5
    assert values[5][4:6] == ["225元(含稅)", "275元(含稅)"]  # E6:F6
    assert values[7][4:6] == ["300元(含稅)", "350元(含稅)"]  # E8:F8
    assert values[8][4:6] == ["350元(含稅)", "400元(含稅)"]  # E9:F9
    assert values[12][2] == "2026/11/05～2026/11/10"  # C13
    assert values[13][2] == "尚未設定"  # C14


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


def test_settings_support_vip_and_nonvip_rates_for_both_parts():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        150,
        200,
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        200,
        250,
        "",
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
        "notice-id",
        300,
        350,
        400,
        450,
    )

    row = _settings_row(settings)
    loaded = _settings_from_row(row)

    assert len(row) == 20
    assert row[3:7] == [150, 200, 300, 350]
    assert row[9:13] == [200, 250, 400, 450]
    assert loaded.phase1_nonvip_weekday_rate == 300
    assert loaded.phase2_nonvip_weekend_rate == 450

    form = deep_clean_settings_form_values(loaded)
    assert form["phase1_start"].isoformat() == "2026-12-15"
    assert form["phase1_weekday_rate"] == 150
    assert form["phase1_nonvip_weekday_rate"] == 300
    assert form["phase2_weekend_rate"] == 250
    assert form["phase2_nonvip_weekend_rate"] == 450


def test_settings_persist_lunar_new_year_holiday_dates():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ), datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        150, 200,
        datetime(2027, 1, 22, tzinfo=TZ), datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        200, 250, "",
        datetime(2026, 11, 5, tzinfo=TZ), datetime(2026, 11, 10, tzinfo=TZ),
        "notice-id", 300, 350, 400, 450,
        datetime(2027, 2, 5, tzinfo=TZ), datetime(2027, 2, 10, 23, 59, tzinfo=TZ),
    )

    loaded = _settings_from_row(_settings_row(settings))

    assert loaded.lunar_new_year_start.date().isoformat() == "2027-02-05"
    assert loaded.lunar_new_year_end.date().isoformat() == "2027-02-10"


def test_legacy_settings_keep_old_rates_as_vip_and_default_nonvip_to_zero():
    legacy_row = [
        2026, "2026-12-15", "2027-01-21", 150, 200,
        "2027-01-22", "2027-02-04", 200, 250, "",
        "2026-11-05", "2026-11-10", "notice-id", "2026-09-08 06:23:23",
    ]

    loaded = _settings_from_row(legacy_row)

    assert loaded.phase1_weekday_rate == 150
    assert loaded.phase2_weekend_rate == 250
    assert loaded.phase1_nonvip_weekday_rate == 0
    assert loaded.phase2_nonvip_weekend_rate == 0


def test_settings_save_repairs_missing_header_and_duplicate_year_rows():
    settings = DeepCleanSettings(
        2026,
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        150,
        200,
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        200,
        250,
        "",
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
        "notice-id",
    )
    malformed = [
        [2026, "old"],
        [2026, "newer-but-replaced"],
        [2025, "preserved"],
    ]

    repaired = _canonical_settings_values(malformed, settings)

    assert repaired[0][0:3] == ["年度", "PART1開始", "PART1結束"]
    assert [row[0] for row in repaired[1:]] == [2025, 2026]
    assert repaired[2][1] == "2026-12-15"


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
