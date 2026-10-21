import pytest

from tools.service_management.booking_gate import build_gate, combine_gates, is_hold, parse_rule
from tools.service_management.vip_schedule_diff import sync_schedule

from tests.test_vip_schedule_diff import Book, MissingSheet, Sheet, old_row, source_row


def rule(**changes):
    record = {"啟用": "TRUE", "地區": "台北", "適用起日": "2026-12-15", "適用迄日": "2027-02-04",
              "確認檔案ID": "https://docs.google.com/spreadsheets/d/FILE123/edit", "確認工作表": "2026大掃除",
              "姓名欄": "C", "電話欄": "D", "地址欄": "F", "確認欄": "V", "確認值": "TRUE", "備註欄": "W",
              "排程排除欄": "A", "排除關鍵字": "改,加", "確認表排除欄": "", "已確認欄": "B", "已確認關鍵字": "已確認"}
    record.update(changes)
    return parse_rule(record)


def confirm_row(name="王小明", phone="0912345678", address="台北市大安區", confirmed="TRUE", note=""):
    row = [""] * 23
    row[2], row[3], row[5], row[21], row[22] = name, phone, address, confirmed, note
    return row


def schedule(date="2027/01/20", service="定期清潔", note="", **kwargs):
    row = old_row(date=date, service=service, **kwargs)
    row[1] = note
    return row


def sync(sheet, rows, gate, name="台北202701"):
    return sync_schedule(Book([sheet]), name, rows, ["標題"] * 30, MissingSheet, gate=gate)


def gate_cells(sheet, row=1):
    header = sheet.data[0]
    values = sheet.data[row] + [""] * len(header)
    return [values[header.index(name)] for name in ("回覆確認", "確認備註", "改加標記", "已確認", "預約條件")]


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


@pytest.mark.parametrize("confirmed,service,note,expected", [
    ("TRUE", "定期清潔", "", ("TRUE", "1/20改3人4", "", "", "可約")),
    ("TRUE", "改", "", ("TRUE", "1/20改3人4", "改", "", "改加")),
    ("FALSE", "定期清潔", "", ("FALSE", "1/20改3人4", "", "", "未回覆")),
    ("FALSE", "3人4小時加", "已確認", ("FALSE", "1/20改3人4", "加", "已確認", "改加")),
    ("FALSE", "定期清潔", "已確認", ("FALSE", "1/20改3人4", "", "已確認", "已確認")),
    ("", "定期清潔", "每月確認", ("空白", "1/20改3人4", "", "", "未回覆")),
])
def test_stage_order_is_change_then_confirmed_then_ready(confirmed, service, note, expected):
    gate = build_gate(rule(), [confirm_row(confirmed=confirmed, note="1/20改3人4")])
    assert gate(schedule(service=service, note=note)) == expected


def test_old_rule_sheet_without_confirmed_columns_defaults_to_b():
    record = {k: v for k, v in {"啟用": "TRUE", "地區": "台北", "適用起日": "2026-12-15", "適用迄日": "2027-02-04",
                                "確認檔案ID": "F", "確認工作表": "2026大掃除", "確認欄": "V"}.items()}
    parsed = parse_rule(record)
    assert parsed["confirmed_col"] == 1 and parsed["confirmed_words"] == ["已確認"]


def test_missing_customer_is_held():
    gate = build_gate(rule(), [confirm_row(phone="0900000000", name="別人")])
    assert gate(schedule()) == ("查無", "", "", "", "未回覆")


def test_matches_same_phone_different_floor_by_address():
    rows = [confirm_row(name="劉家熙-4樓", address="台北市內湖區4樓", confirmed="TRUE"),
            confirm_row(name="劉家熙-6樓", address="台北市內湖區6樓", confirmed="FALSE")]
    gate = build_gate(rule(), rows)
    four = schedule(); four[2], four[4] = "劉家熙-4樓", "台北市內湖區4樓"
    six = schedule(); six[2], six[4] = "劉家熙-6樓", "台北市內湖區6樓"
    assert gate(four)[4] == "可約" and gate(six)[4] == "未回覆"


def test_combined_rules_hold_if_any_rule_holds():
    gate = combine_gates([build_gate(rule(), [confirm_row()]),
                          build_gate(rule(確認工作表="其他"), [confirm_row(confirmed="FALSE")])])
    assert gate(schedule()) == ("TRUE、FALSE", "", "", "", "未回覆")


def test_sync_writes_four_columns_as_text_and_keeps_status():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="=1+1")]))
    assert sheet.data[1][8] == "未安排"
    assert gate_cells(sheet) == ["FALSE", "=1+1", "", "", "未回覆"]
    assert any(kwargs.get("value_input_option") == "RAW" and any("=1+1" in str(u["values"]) for u in updates)
               for updates, kwargs in sheet.batches)
    assert result["booking_gate"] == {"enabled": True, "held": 1, "held_rows": [2], "stages": {"未回覆": 1}}


def test_sync_releases_after_confirmation():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE")]))
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row()]))
    assert gate_cells(sheet) == ["TRUE", "", "", "", "可約"]
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
    assert sheet.data[1][8] == "未安排" and gate_cells(sheet)[4] == "未回覆"
    assert result["booking_gate"]["held_rows"] == [2]


def test_month_outside_rule_period_gets_no_gate_columns():
    original = schedule(date="2026/11/10")
    sheet = Sheet("台北202611", [original])
    result = sync(sheet, [source_row(original, **{"0": "2人"})], build_gate(rule(), [confirm_row(confirmed="FALSE")]),
                  name="台北202611")
    assert not set(sheet.data[0]) & {"回覆確認", "確認備註", "改加標記", "已確認", "預約條件"}
    assert sheet.data[1][8] == "未安排" and result["booking_gate"]["held"] == 0


def test_rule_turned_off_clears_existing_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="x")]))
    sync(sheet, [source_row(original)], None)
    assert gate_cells(sheet) == ["", "", "", "", ""]


def test_sync_without_rule_adds_no_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original, **{"0": "2人"})], None)
    assert "預約條件" not in sheet.data[0]


def test_rule_sheet_is_created_with_header_notes(monkeypatch):
    from unittest.mock import MagicMock
    import tools.service_management.booking_gate as gate_module

    class Missing(Exception):
        pass

    ss = MagicMock()
    ss.worksheet.side_effect = Missing()
    sheet = ss.add_worksheet.return_value
    sheet.id = 7
    sheet.get_all_values.return_value = [gate_module.RULE_HEADERS] + gate_module.EXAMPLE_RULES
    monkeypatch.setattr(gate_module, "_master_spreadsheet", lambda gc: ss)
    rules = gate_module.load_rules(None, "台北", Missing)
    assert len(rules) == 1 and rules[0]["confirm_letter"] == "V"
    request = ss.batch_update.call_args.args[0]["requests"][0]["updateCells"]
    notes = [cell["note"] for cell in request["rows"][0]["values"]]
    assert request["range"]["sheetId"] == 7 and request["fields"] == "note"
    assert len(notes) == len(gate_module.RULE_HEADERS) and all(notes)
    assert notes[0].startswith("TRUE＝更新排程工作表時套用這一列規則")


def test_existing_confirm_status_column_is_renamed_and_reused():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sheet.data[0] += [""] * (37 - len(sheet.data[0])) + ["確認狀態", "確認備註", "改加標記", "預約條件"]
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row()]))
    assert "確認狀態" not in sheet.data[0]
    assert sheet.data[0].index("回覆確認") == 37
    assert gate_cells(sheet) == ["TRUE", "", "", "", "可約"]


def test_is_hold_means_every_stage_except_ready():
    assert [is_hold(s) for s in ("改加", "已確認", "可約", "未回覆", "")] == [True, True, False, True, False]
