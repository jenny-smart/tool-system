from copy import deepcopy

import pytest

from tools.service_management.booking_gate import build_gate, is_hold, parse_rule

from tests.test_vip_schedule_diff import Book, MissingSheet, Sheet, old_row, source_row
from tools.service_management.vip_schedule_diff import sync_schedule


def rule(**changes):
    record = {"啟用": "TRUE", "地區": "台北", "適用起日": "2026-12-15", "適用迄日": "2027-02-04",
              "確認檔案ID": "https://docs.google.com/spreadsheets/d/FILE123/edit", "確認工作表": "2026大掃除",
              "姓名欄": "C", "電話欄": "D", "地址欄": "F", "確認欄": "V", "確認值": "TRUE", "備註欄": "W",
              "排程排除欄": "A", "排除關鍵字": "改,加", "確認表排除欄": ""}
    record.update(changes)
    return parse_rule(record)


def confirm_row(name="王小明", phone="0912345678", address="台北市大安區", confirmed="TRUE", note=""):
    row = [""] * 23
    row[2], row[3], row[5], row[21], row[22] = name, phone, address, confirmed, note
    return row


def schedule(date="2027/01/20", service="定期清潔", **kwargs):
    return old_row(date=date, service=service, **kwargs)


def test_rule_reads_columns_and_url():
    parsed = rule()
    assert parsed["spreadsheet_id"] == "FILE123"
    assert parsed["confirm_col"] == 21 and parsed["note_col"] == 22
    assert parsed["exclude_words"] == ["改", "加"]


def test_outside_period_is_not_affected():
    gate = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    assert gate(schedule(date="2026/12/14")) == ""
    assert gate(schedule(date="2027/02/05")) == ""


def test_confirmed_customer_can_book_and_note_is_shown():
    gate = build_gate(rule(), [confirm_row(note="1/20改3人4")])
    reason = gate(schedule())
    assert not is_hold(reason) and "1/20改3人4" in reason


def test_unconfirmed_or_missing_customer_is_held():
    assert is_hold(build_gate(rule(), [confirm_row(confirmed="FALSE")])(schedule()))
    assert "找不到" in build_gate(rule(), [confirm_row(phone="0900000000", name="別人")])(schedule())


def test_schedule_mark_holds_even_when_confirmed():
    gate = build_gate(rule(), [confirm_row()])
    assert "標記「改」" in gate(schedule(service="改"))
    assert "標記「加」" in gate(schedule(service="3人4小時加"))


def test_matches_same_phone_different_floor_by_address():
    rows = [confirm_row(name="劉家熙-4樓", address="台北市內湖區4樓", confirmed="TRUE"),
            confirm_row(name="劉家熙-6樓", address="台北市內湖區6樓", confirmed="FALSE")]
    gate = build_gate(rule(), rows)
    four = schedule(); four[2], four[4] = "劉家熙-4樓", "台北市內湖區4樓"
    six = schedule(); six[2], six[4] = "劉家熙-6樓", "台北市內湖區6樓"
    assert not is_hold(gate(four)) and is_hold(gate(six))


def test_bad_column_letter_is_rejected():
    with pytest.raises(ValueError):
        rule(確認欄="V欄")


def test_sync_keeps_status_and_releases_note_after_confirmation():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    hold = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    sync_schedule(Book([sheet]), "台北202701", [source_row(original)], ["標題"] * 30, MissingSheet, gate=hold)
    column = sheet.data[0].index("預約條件")
    assert sheet.data[1][8] == "未安排" and sheet.data[1][column].startswith("先不約")
    release = build_gate(rule(), [confirm_row()])
    result = sync_schedule(Book([sheet]), "台北202701", [source_row(original)], ["標題"] * 30, MissingSheet, gate=release)
    assert sheet.data[1][8] == "未安排" and sheet.data[1][column].startswith("可預約")
    assert result["booking_gate"]["held"] == 0


def test_booked_rows_get_no_booking_condition():
    booked = schedule(order="LC123")
    sheet = Sheet("台北202701", [booked])
    hold = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    result = sync_schedule(Book([sheet]), "台北202701", [source_row(booked)], ["標題"] * 30, MissingSheet, gate=hold)
    assert "預約條件" not in sheet.data[0]
    assert result["booking_gate"]["held"] == 0


def test_new_calendar_row_gets_hold_note_but_stays_unarranged():
    sheet = Sheet("台北202701", [])
    hold = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    result = sync_schedule(Book([sheet]), "台北202701", [source_row(schedule())], ["標題"] * 30, MissingSheet, gate=hold)
    column = sheet.data[0].index("預約條件")
    assert sheet.data[1][8] == "未安排" and sheet.data[1][column].startswith("先不約")
    assert result["booking_gate"]["held_rows"] == [2]


def test_sync_writes_booking_condition_column_and_reports_held_rows():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    gate = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    result = sync_schedule(Book([sheet]), "台北202701", [source_row(original)], ["標題"] * 30, MissingSheet, gate=gate)
    column = sheet.data[0].index("預約條件")
    assert sheet.data[1][8] == "未安排"
    assert sheet.data[1][column].startswith("先不約")
    assert result["booking_gate"] == {"enabled": True, "held": 1, "held_rows": [2]}


def test_sync_without_rule_adds_no_booking_condition_column():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync_schedule(Book([sheet]), "台北202701", [source_row(original, **{"0": "2人"})], ["標題"] * 30, MissingSheet)
    assert "預約條件" not in sheet.data[0]


def test_month_outside_rule_period_gets_no_booking_condition_column():
    original = schedule(date="2026/11/10")
    sheet = Sheet("台北202611", [original])
    gate = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    result = sync_schedule(Book([sheet]), "台北202611", [source_row(original, **{"0": "2人"})], ["標題"] * 30,
                           MissingSheet, gate=gate)
    assert "預約條件" not in sheet.data[0]
    assert sheet.data[1][8] == "未安排"
    assert result["booking_gate"]["held"] == 0
