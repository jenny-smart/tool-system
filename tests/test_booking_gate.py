import pytest

from tools.service_management.booking_gate import build_gate, combine_gates, is_hold, parse_rule
from tools.service_management.vip_schedule_diff import sync_schedule

from tests.test_vip_schedule_diff import Book, MissingSheet, Sheet, old_row, source_row


def rule(start="2026-12-01", end="2027-02-28", short_periods=None, **changes):
    record = {"地區": "台北",
              "確認檔案ID": "https://docs.google.com/spreadsheets/d/FILE123/edit", "確認工作表": "2026大掃除",
              "姓名欄": "C", "電話欄": "D", "地址欄": "F", "確認欄": "V", "確認值": "TRUE", "備註欄": "W",
              "排程排除欄": "A", "排除關鍵字": "改,加", "確認表排除欄": "", "已確認欄": "B", "已確認關鍵字": "已確認"}
    record.update(changes)
    return parse_rule(record, start, end, short_periods)


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
    return [values[header.index(name)] for name in ("回覆確認", "確認備註", "改加標記", "已確認", "人時", "預約條件")]


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
    assert gate(schedule(date="2026/11/30")) is None
    assert gate(schedule(date="2027/03/01")) is None
    assert gate(schedule(date="2027/02/26"))[-1] == "未回覆"


@pytest.mark.parametrize("confirmed,service,note,expected", [
    ("TRUE", "定期清潔", "", ("TRUE", "1/20改3人4", "", "", "6", "可約")),
    ("TRUE", "改", "", ("TRUE", "1/20改3人4", "改", "", "6", "改加")),
    ("FALSE", "定期清潔", "", ("FALSE", "1/20改3人4", "", "", "6", "未回覆")),
    ("FALSE", "3人4小時加", "已確認", ("FALSE", "1/20改3人4", "加", "已確認", "9", "改加")),
    ("FALSE", "定期清潔", "已確認", ("FALSE", "1/20改3人4", "", "已確認", "6", "已確認")),
    ("", "定期清潔", "每月確認", ("空白", "1/20改3人4", "", "", "6", "未回覆")),
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
    assert gate(schedule()) == ("查無", "", "", "", "6", "未回覆")


def test_matches_same_phone_different_floor_by_address():
    rows = [confirm_row(name="劉家熙-4樓", address="台北市內湖區4樓", confirmed="TRUE"),
            confirm_row(name="劉家熙-6樓", address="台北市內湖區6樓", confirmed="FALSE")]
    gate = build_gate(rule(), rows)
    four = schedule(); four[2], four[4] = "劉家熙-4樓", "台北市內湖區4樓"
    six = schedule(); six[2], six[4] = "劉家熙-6樓", "台北市內湖區6樓"
    assert gate(four)[-1] == "可約" and gate(six)[-1] == "未回覆"


def test_combined_rules_hold_if_any_rule_holds():
    gate = combine_gates([build_gate(rule(), [confirm_row()]),
                          build_gate(rule(確認工作表="其他"), [confirm_row(confirmed="FALSE")])])
    assert gate(schedule()) == ("TRUE、FALSE", "", "", "", "6", "未回覆")


def test_sync_writes_four_columns_as_text_and_keeps_status():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="=1+1")]))
    assert sheet.data[1][8] == "未安排"
    assert gate_cells(sheet) == ["FALSE", "=1+1", "", "", "6", "未回覆"]
    assert any(kwargs.get("value_input_option") == "RAW" and any("=1+1" in str(u["values"]) for u in updates)
               for updates, kwargs in sheet.batches)
    assert result["booking_gate"] == {"enabled": True, "held": 1, "held_rows": [2], "stages": {"未回覆": 1}}


def test_sync_releases_after_confirmation():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE")]))
    result = sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row()]))
    assert gate_cells(sheet) == ["TRUE", "", "", "", "6", "可約"]
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
    assert sheet.data[1][8] == "未安排" and gate_cells(sheet)[5] == "未回覆"
    assert result["booking_gate"]["held_rows"] == [2]


def test_month_outside_rule_period_gets_no_gate_columns():
    original = schedule(date="2026/11/10")
    sheet = Sheet("台北202611", [original])
    # A November-only run passes its own period; the row is outside a December-only gate.
    result = sync(sheet, [source_row(original, **{"0": "2人"})],
                  build_gate(rule("2026-12-01", "2026-12-31"), [confirm_row(confirmed="FALSE")]), name="台北202611")
    assert not set(sheet.data[0]) & {"回覆確認", "確認備註", "改加標記", "已確認", "人時", "預約條件"}
    assert sheet.data[1][8] == "未安排" and result["booking_gate"]["held"] == 0


def test_rule_turned_off_clears_existing_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original)], build_gate(rule(), [confirm_row(confirmed="FALSE", note="x")]))
    sync(sheet, [source_row(original)], None)
    assert gate_cells(sheet) == ["", "", "", "", "", ""]


def test_sync_without_rule_adds_no_columns():
    original = schedule()
    sheet = Sheet("台北202701", [original])
    sync(sheet, [source_row(original, **{"0": "2人"})], None)
    assert "預約條件" not in sheet.data[0]


class FakeSettingsSheet:
    def __init__(self, values):
        self.values, self.id, self.col_count = values, 7, len(values[0]) if values else 0

    def get_all_values(self):
        return [list(row) for row in self.values]

    def clear(self):
        self.values = []

    def resize(self, cols):
        self.col_count = cols

    def update(self, values, range_name, **kwargs):
        self.values = [list(row) for row in values]


def _settings_book(monkeypatch, values=None):
    from unittest.mock import MagicMock
    import tools.service_management.booking_gate as gate_module

    class Missing(Exception):
        pass

    ss = MagicMock()
    sheet = FakeSettingsSheet(values) if values is not None else None
    ss.worksheet.side_effect = (lambda name: sheet) if sheet else Missing()
    ss.add_worksheet.side_effect = lambda **kw: FakeSettingsSheet([])
    monkeypatch.setattr(gate_module, "_master_spreadsheet", lambda gc: ss)
    return gate_module, ss, Missing


def test_settings_default_to_taipei_deep_clean_when_sheet_missing(monkeypatch):
    gate_module, _, missing = _settings_book(monkeypatch)
    settings = gate_module.load_settings(None, "台北", missing)
    assert settings["確認工作表"] == "2026大掃除" and settings["確認欄"] == "V" and settings["已確認欄"] == "B"
    assert gate_module.load_settings(None, "台中", missing)["確認檔案ID"] == ""


def test_old_rule_sheet_is_read_and_rewritten_with_current_columns(monkeypatch):
    old_header = ["啟用", "地區", "適用起日", "適用迄日", "確認檔案ID", "確認工作表", "確認欄", "確認值", "說明"]
    values = [old_header, ["TRUE", "台北", "2026-12-15", "2027-02-04", "FILE_TP", "2026大掃除", "X", "Y", "x"],
              ["TRUE", "台中", "2026-12-15", "2027-02-04", "FILE_TC", "台中大掃除", "V", "TRUE", ""]]
    gate_module, ss, missing = _settings_book(monkeypatch, values)
    settings = gate_module.load_settings(None, "台北", missing)
    assert (settings["確認檔案ID"], settings["確認欄"], settings["確認值"], settings["已確認欄"]) == ("FILE_TP", "X", "Y", "B")
    gate_module.save_settings(None, "台北", {**settings, "確認欄": "V", "確認值": "TRUE"}, missing)
    sheet = ss.worksheet("排程預約條件")
    assert sheet.values[0] == gate_module.RULE_HEADERS
    rows = {row[0]: dict(zip(gate_module.RULE_HEADERS, row)) for row in sheet.values[1:]}
    assert rows["台中"]["確認檔案ID"] == "FILE_TC" and rows["台北"]["確認欄"] == "V" and rows["台北"]["更新時間"]
    notes = ss.batch_update.call_args.args[0]["requests"][0]["updateCells"]["rows"][0]["values"]
    assert len(notes) == len(gate_module.RULE_HEADERS) and all(cell["note"] for cell in notes)


def test_save_rejects_missing_source_before_writing(monkeypatch):
    gate_module, ss, missing = _settings_book(monkeypatch)
    with pytest.raises(ValueError):
        gate_module.save_settings(None, "台中", gate_module.load_settings(None, "台中", missing), missing)
    ss.add_worksheet.assert_not_called()


def test_area_gate_uses_run_period(monkeypatch):
    from unittest.mock import MagicMock
    gate_module, _, missing = _settings_book(monkeypatch)
    source = MagicMock()
    source.worksheet.return_value.get_all_values.return_value = [["header"], confirm_row()]
    monkeypatch.setattr(gate_module, "_open_source", lambda gc, sid: source)
    gate, descriptions = gate_module.load_area_gate(None, "台北", "2026-12-01", "2027-02-28", worksheet_not_found=missing)
    assert descriptions == ["2026大掃除 2026-12-01～2027-02-28"]
    assert gate(schedule(date="2027/02/26"))[-1] == "可約" and gate(schedule(date="2027/03/01")) is None


def test_blank_period_means_no_limit_and_reversed_period_is_rejected():
    gate = build_gate(rule("", ""), [confirm_row()])
    assert gate(schedule(date="2026/11/01"))[-1] == "可約"
    assert gate(schedule(date="2027/02/26"))[-1] == "可約"
    with pytest.raises(ValueError):
        rule("2027-03-01", "2027-02-28")


def test_ui_defaults_on_during_deep_clean_and_returns_settings(monkeypatch):
    from datetime import date, datetime
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import tools.service_management.booking_gate as gate_module
    import tools.service_management.booking_gate_ui as ui

    st = MagicMock()
    st.session_state = {}
    st.button.return_value = False
    st.columns.side_effect = lambda n: [MagicMock(text_input=lambda label, value, **kw: value) for _ in range(n)]
    st.checkbox.side_effect = lambda label, value, key: value
    season = SimpleNamespace(season_year=2026, phase1_start=datetime(2026, 12, 15), phase2_end=datetime(2027, 2, 4))
    monkeypatch.setattr(ui, "deep_clean_overlap", lambda s, e: season if e >= date(2026, 12, 15) else None)
    monkeypatch.setattr(gate_module, "load_settings", lambda gc, area: {"地區": area, **gate_module.DEFAULT_SETTINGS,
                                                                       "確認工作表": "2026大掃除"})
    monkeypatch.setattr("tools.service_management.stored_value._gc", lambda: None)

    state = ui.render(st, ["台北"], date(2026, 12, 1), date(2027, 2, 28))
    assert state["enabled"] is True and state["settings"]["台北"]["確認工作表"] == "2026大掃除"
    assert "2026 年節大掃除期間" in st.caption.call_args_list[0].args[0]
    off = ui.render(st, ["台北"], date(2026, 11, 1), date(2026, 11, 30))
    assert off == {"enabled": False, "settings": {}}
    assert ui.render(st, ["台北"], None, None) is None


@pytest.mark.parametrize("service,start,end,hours,stage", [
    ("1人4小時", "14:00", "18:00", "4", "人時不足"),
    ("2人", "9:00", "11:00", "4", "人時不足"),
    ("定期清潔", "9:00", "12:00", "6", "可約"),
    ("3人4小時", "14:00", "18:00", "12", "可約"),
    ("2人2或2人3", "14:00", "16:00", "4", "人時不足"),
    ("改", "9:00", "11:00", "4", "人時不足"),
    ("改", "9:00", "12:00", "6", "改加"),
])
def test_person_hours_below_minimum_is_held(service, start, end, hours, stage):
    row = schedule(service=service, start=start)
    row[7] = end
    result = build_gate(rule(), [confirm_row()])(row)
    assert result[4] == hours and result[5] == stage


def test_minimum_person_hours_is_configurable_and_blank_disables():
    row = schedule(service="1人4小時", start="14:00")
    row[7] = "18:00"
    assert build_gate(rule(最低人時="4"), [confirm_row()])(row)[5] == "可約"
    assert build_gate(rule(最低人時=""), [confirm_row()])(row)[5] == "可約"
    # 不檢查人時後，4 人時的列才依改加、已確認、未回覆判斷。
    changed = schedule(service="1人4小時改", start="14:00")
    changed[7] = "18:00"
    assert build_gate(rule(), [confirm_row()])(changed)[5] == "人時不足"
    assert build_gate(rule(最低人時=""), [confirm_row()])(changed)[5] == "改加"
    assert build_gate(rule(最低人時=""), [confirm_row(confirmed="FALSE")])(row)[5] == "未回覆"
    with pytest.raises(ValueError):
        rule(最低人時="六")


def test_person_hours_only_checked_inside_deep_clean_period():
    # 匯出 12/1～2/28 讓客人看到完整四個月；大掃除期間 12/15～2/4 才檢查人時。
    gate = build_gate(rule(short_periods=[("2026-12-15", "2027-02-04")]), [confirm_row()])
    for date, stage in (("2026/12/01", "可約"), ("2026/12/15", "人時不足"), ("2027/01/20", "人時不足"),
                        ("2027/02/04", "人時不足"), ("2027/02/26", "可約")):
        row = schedule(date=date, service="1人4小時", start="14:00")
        row[7] = "18:00"
        assert gate(row)[4:] == ("4", stage), date
    # 匯出期間沒有大掃除設定時不檢查人時。
    row = schedule(service="1人4小時", start="14:00")
    row[7] = "18:00"
    assert build_gate(rule(short_periods=[]), [confirm_row()])(row)[5] == "可約"
