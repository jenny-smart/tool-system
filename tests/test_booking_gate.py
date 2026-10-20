import pytest

from tools.service_management.booking_gate import build_gate, combine_gates, is_hold, parse_rule
from tools.service_management.vip_schedule_diff import sync_schedule

from tests.test_vip_schedule_diff import Book, MissingSheet, Sheet, old_row, source_row


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


def sync(sheet, rows, gate, name="台北202701"):
    return sync_schedule(Book([sheet]), name, rows, ["標題"] * 30, MissingSheet, gate=gate)


def gate_cells(sheet, row=1):
    header = sheet.data[0]
    values = sheet.data[row] + [""] * len(header)
    return [values[header.index(name)] for name in ("確認狀態", "確認備註", "改加標記", "預約條件")]


def test_rule_reads_columns_and_url():
    parsed = rule()
    assert parsed["spreadsheet_id"] == "FILE123"
    assert parsed["confirm_col"] == 21 and parsed["note_col"] == 22
    assert parsed["exclude_words"] == ["改", "加"]


def test_bad_column_letter_is_rejected():
    with pytest.raises(ValueError):
        rule(確認欄="V欄")


def test_outside_period_is_not_affected():
    gate = build_gate(rule(), [confirm_row(confirmed="FALSE")])
    assert gate(schedule(date="2026/12/14")) is None
    assert gate(schedule(date="2027/02/05")) is None


@pytest.mark.parametrize("confirmed,service,expected", [
    ("TRUE", "定期清潔", ("TRUE", "1/20改3人4", "", "")),
    ("TRUE", "改", ("TRUE", "1/20改3人4", "改", "先不約")),
    ("FALSE", "定期清潔", ("FALSE", "1/20改3人4", "", "先不約")),
    ("FALSE", "3人4小時加", ("FALSE", "1/20改3人4", "加", "先不約")),
    ("", "定期清潔", ("空白", "1/20改3人4", "", "先不約")),
])
def test_four_values_from_v_w_and_a(confirmed, service, expected):
    gate = build_gate(rule(), [confirm_row(confirmed=confirmed, note="1/20改3人4")])
    assert gate(schedule(service=service)) == expected


def test_missing_customer_is_held():
    gate = build_gate(rule(), [confirm_row(phone="0900000000", name="別人")])
    assert gate(schedule()) == ("查無", "", "", "先不約")


def test_matches_same_phone_different_floor_by_address():
    rows = [confirm_row(name="劉家熙-4樓", address="台北市內湖區4樓", confirmed="TRUE"),
            confirm_row(name="劉家熙-6樓", address="台北市內湖區6樓", confirmed="FALSE")]
    gate = build_gate(rule(), rows)
    four = schedule(); four[2], four[4] = "劉家熙-4樓", "台北市內湖區4樓"
    six = schedule(); six[2], six[4] = "劉家熙-6樓", "台北市內湖區6樓"
    assert not is_hold(gate(four)[3]) and is_hold(gate(six)[3])


def test_combined_rules_hold_if_any_rule_holds():
    gate = combine_gates([build_gate(rule(), [confirm_row()]),
                          build_gate(rule(確認工作表="其他"), [confirm_row(confirmed="FALSE")])])
    assert gate(schedule()) == ("TRUE、FALSE", "", "", "先不約")


def test_sync_writes_four_columns_as_text_and_keeps_status():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="=1+1")]))
    assert sheet.data[1][8] == "未安排"
    assert gate_cells(sheet) == ["FALSE", "=1+1", "", "先不約"]
    assert any(kwargs.get("value_input_option") == "RAW" and any("=1+1" in str(u["values"]) for u in updates)
               for updates, kwargs in sheet.batches)
    assert result["booking_gate"] == {"enabled": True, "held": 1, "held_rows": [2]}


def test_sync_releases_after_confirmation():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE")]))
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row()]))
    assert gate_cells(sheet) == ["TRUE", "", "", ""]
    assert result["booking_gate"]["held"] == 0


def test_booked_rows_get_no_gate_columns():
    booked = schedule(order="LC123")
    sheet = Sheet("台北202701", [booked])
    result = sync(sheet, [source_row(booked)], build_gate(rule(), [confirm_row(confirmed="FALSE")]))
    assert "預約條件" not in sheet.data[0]
    assert result["booking_gate"]["held"] == 0


def test_new_calendar_row_gets_values_but_stays_unarranged():
    sheet = Sheet("台北202701", [])
    result = sync(sheet, [source_row(schedule())], build_gate(rule(), [confirm_row(confirmed="FALSE")]))
    assert sheet.data[1][8] == "未安排" and gate_cells(sheet)[3] == "先不約"
    assert result["booking_gate"]["held_rows"] == [2]


def test_month_outside_rule_period_gets_no_gate_columns():
    original = schedule(date="2026/11/10")
    sheet = Sheet("台北202611", [original])
    result = sync(sheet, [source_row(original, **{"0": "2人"})], build_gate(rule(), [confirm_row(confirmed="FALSE")]),
                  name="台北202611")
    assert not set(sheet.data[0]) & {"確認狀態", "確認備註", "改加標記", "預約條件"}
    assert sheet.data[1][8] == "未安排" and result["booking_gate"]["held"] == 0


def test_rule_turned_off_clears_existing_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="x")]))
    sync(sheet, [source_row(original)], None)
    assert gate_cells(sheet) == ["", "", "", ""]


def test_sync_without_rule_adds_no_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original, **{"0": "2人"})], None)
    assert "預約條件" not in sheet.data[0]
