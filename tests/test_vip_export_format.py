import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).parents[1] / "tools/service_management/stored_value.py"


def _load_functions(*names):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", "").startswith("TOPUP_") for t in node.targets))
    ]
    namespace = {
        "Any": object,
        "datetime": datetime,
        "re": re,
        "gspread": SimpleNamespace(Client=object, WorksheetNotFound=KeyError),
        "normalize_name": lambda value: str(value).strip(),
        "log": SimpleNamespace(info=lambda *args: None),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def test_normalize_phone_adds_leading_zero_and_keeps_ten_digits():
    normalize_phone = _load_functions("normalize_phone")["normalize_phone"]
    assert normalize_phone("912345678") == "0912345678"
    assert normalize_phone("0912-345-678") == "0912345678"


def test_schedule_builder_passes_compare_only_to_safe_writer():
    from unittest.mock import patch
    functions = _load_functions("_build_vip_schedule_sheet")
    functions["VIP_SCHEDULE_FALLBACK_HEADERS"] = [""] * 30
    functions["json"] = __import__("json")
    source = SimpleNamespace(get=lambda _range: [["data"]])
    book = SimpleNamespace(worksheet=lambda _name: source)
    client = SimpleNamespace(open_by_key=lambda _key: book)
    result = {"sheet": "台北202611", "count": 1, "differences": {}, "report": "差異"}
    with patch("tools.service_management.vip_schedule_diff.sync_schedule", return_value=result) as writer, \
            patch("tools.service_management.booking_gate.load_area_gate", return_value=(None, [])):
        assert functions["_build_vip_schedule_sheet"](
            client, {"name": "台北", "target_spreadsheet_id": "test"}, "202611", compare_only=True
        ) == result
    assert writer.call_args.kwargs["compare_only"] is True
    assert writer.call_args.args[1] == "台北202611"


def test_schedule_builder_uses_combined_cross_month_source_and_target():
    from unittest.mock import patch
    functions = _load_functions("_build_vip_schedule_sheet")
    functions.update({
        "VIP_SCHEDULE_FALLBACK_HEADERS": [""] * 30,
        "json": __import__("json"),
        "re": re,
    })
    functions["_purchase_item_lookup"] = lambda area: None
    source = SimpleNamespace(
        title="定期VIP_台北_202612-202702", col_count=21,
        get=lambda cell_range: [["2026-12-01", "2027-02-28"]] if cell_range == "T2:U2" else [["data"]],
    )

    book = SimpleNamespace(worksheet=lambda name: source, worksheets=lambda: [source])
    client = SimpleNamespace(open_by_key=lambda _key: book)
    result = {"sheet": "台北202701", "count": 1, "differences": {}, "report": "差異"}
    with patch("tools.service_management.vip_schedule_diff.sync_schedule", return_value=result) as writer, \
            patch("tools.service_management.booking_gate.load_area_gate", return_value=(None, [])):
        assert functions["_build_vip_schedule_sheet"](
            client, {"name": "台北", "target_spreadsheet_id": "test"}, "202612", "202702"
        ) == result
    assert writer.call_args.args[1] == "台北202612-202702"
    assert writer.call_args.args[2] == [["data"]]
    assert writer.call_args.kwargs["period_filter"] == ""


def test_schedule_customer_key_uses_name_phone_and_address():
    functions = _load_functions(
        "normalize_text", "normalize_name_for_compare", "normalize_phone", "normalize_address",
        "_schedule_customer_key",
    )
    assert functions["_schedule_customer_key"](
        ["", "", " 王小明 ", "912-345-678", "台北市 中山區"]
    ) == ("王小明", "0912345678", "台北市 中山區")


def test_vip_writer_sorts_by_column_c_and_writes_phone_as_raw_text():
    functions = _load_functions(
        "normalize_text", "normalize_name_for_compare", "normalize_address", "normalize_phone", "_write_vip_sheet"
    )
    functions["_write_topup_list"] = lambda *args: ""

    class Sheet:
        def __init__(self):
            self.updates = []

        def clear(self):
            pass

        def update(self, **kwargs):
            self.updates.append(kwargs)

        def freeze(self, **kwargs):
            pass

        def batch_format(self, formats):
            self.formats = formats

    sheet = Sheet()
    client = SimpleNamespace(
        open_by_key=lambda _key: SimpleNamespace(worksheet=lambda _name: sheet)
    )
    base = {
        "service": "2人", "note": "", "address": "", "date_str": "2026/10/01",
        "start_str": "08:00", "end_str": "12:00", "status": "已安排",
        "weekday": "四", "price": 600, "person_hrs": 8, "amount": 4800,
        "subtotal": 4800, "balance": 5000, "diff": 200, "line": "",
        "start_dt": datetime(2026, 10, 1, 8, tzinfo=timezone.utc),
    }
    rows = [
        {**base, "name": "王小明", "phone": "912345678", "event_id": "event-wang"},
        {**base, "name": "李小華", "phone": "0987654321", "event_id": "event-li"},
    ]

    functions["_write_vip_sheet"](
        client, "台北", rows, datetime(2026, 10, 1), area_target_id="sheet-id"
    )

    # 姓氏總筆劃：王（4）在李（7）前面。
    assert [row[2] for row in sheet.updates[0]["values"][1:]] == ["王小明", "李小華"]
    assert sheet.updates[1] == {
        "values": [["0912345678"], ["0987654321"]],
        "range_name": "D2:D3",
        "value_input_option": "RAW",
    }
    assert sheet.formats[0]["range"] == "A2:S200"
    assert [row[18] for row in sheet.updates[0]["values"][1:]] == ["event-wang", "event-li"]
    assert sheet.updates[2]["range_name"] == "T1:U2"
    # Export itself also highlights both active and paused same-day visits.
    duplicate = {**rows[0], "address": "台北市", "event_id": "event-wang-paused", "status": "暫停"}
    rows[0]["address"] = "台北市"
    sheet.updates.clear()
    functions["_write_vip_sheet"](client, "台北", rows + [duplicate], datetime(2026, 10, 1), area_target_id="sheet-id")
    assert [item["range"] for item in sheet.formats[1:]] == ["A2:S2", "A3:S3"]
    assert sheet.updates[0]["values"][2][8] == "暫停"



def test_calendar_export_writes_only_combined_cross_month_sheet():
    from datetime import timedelta
    from unittest.mock import MagicMock
    functions = _load_functions("_month_keys", "step2_export_vip_calendar")
    writer = MagicMock(side_effect=lambda _gc, _area, _rows, start, **kwargs: start.strftime("%Y%m"))
    functions.update({
        "timedelta": timedelta,
        "now_tp": lambda: datetime(2026, 10, 4, tzinfo=timezone.utc),
        "checkin_both": lambda *args: None,
        "_calendar_service": lambda: None,
        "_load_deep_clean_pricing_settings": lambda *_: [],
        "_fetch_calendar_events": lambda *args: [],
        "_process_events": lambda *args: [],
        "_load_stored_value_info": MagicMock(),
        "_enrich_with_stored_value": lambda rows, info: rows,
        "_write_vip_sheet": writer,
        "json": __import__("json"),
        "log": MagicMock(),
    })
    result = functions["step2_export_vip_calendar"](
        None, [{"name": "台北", "calendar_id": "calendar"}], "run",
        datetime(2026, 11, 10, tzinfo=timezone.utc), datetime(2026, 12, 20, tzinfo=timezone.utc),
    )
    assert result["台北"]["count"] == 0
    assert writer.call_count == 1
    assert writer.call_args.args[2] == []
    assert writer.call_args.kwargs["sheet_name"] == "定期VIP_台北_202611-202612"
    assert writer.call_args.kwargs["end_dt"].strftime("%Y%m%d") == "20261220"
    functions["_load_stored_value_info"].assert_not_called()


def test_cross_month_balance_uses_monthly_subtotal_and_cumulative_difference():
    functions = _load_functions("normalize_text", "normalize_name_for_compare", "_enrich_with_stored_value",
                                "_mark_topup_rows")
    rows = [
        {"name": "王小明", "amount": 3000, "start_dt": datetime(2026, 12, 1)},
        {"name": "王小明", "amount": 4000, "start_dt": datetime(2027, 1, 1)},
        {"name": "王小明", "amount": 2000, "start_dt": datetime(2027, 2, 1)},
    ]
    result = functions["_enrich_with_stored_value"](
        rows, {"王小明": {"totalBalance": 8000, "lineValue": "line"}}
    )
    assert [row["subtotal"] for row in result] == [3000, 4000, 2000]
    assert [row["diff"] for row in result] == [5000, 1000, -1000]


def test_same_customer_same_day_events_keep_distinct_ids_and_times():
    functions = _load_functions("_process_events")
    functions.update({
        "TZ_TAIPEI": timezone.utc,
        "parse_title": lambda _: {"name": "王小明", "phone": "0912345678", "service": "1人", "note": ""},
        "get_status": lambda _: "未安排",
        "normalize_address": lambda value: value,
        "normalize_phone": lambda value: value,
        "get_weekday_text": lambda _: "二",
        "get_price_by_date": lambda *_: 600,
        "parse_service_people": lambda _: 1,
        "calc_hours": lambda start, end: (end - start).total_seconds() / 3600,
    })
    events = [
        {"id": event_id, "summary": "王小明,0912345678", "location": "台北市", "colorId": "3",
         "start": {"dateTime": f"2026-11-10T{start}:00:00+00:00"},
         "end": {"dateTime": f"2026-11-10T{end}:00:00+00:00"}}
        for event_id, start, end in (("morning", "09", "12"), ("afternoon", "14", "17"))
    ]
    events[1]["_schedule_status"] = "暫停"
    rows = functions["_process_events"](events, "台北", "calendar")
    assert [row["event_id"] for row in rows] == ["calendar:morning", "calendar:afternoon"]
    assert [row["start_str"] for row in rows] == ["09:00", "14:00"]
    assert [row["status"] for row in rows] == ["未安排", "暫停"]
    # 暫停不計服務金額（空白）；未安排照常計入。
    assert [row["amount"] for row in rows] == [1800, ""]


def test_deep_clean_price_uses_vip_part_and_day_type_rates():
    get_price_by_date = _load_functions("get_price_by_date")["get_price_by_date"]
    setting = SimpleNamespace(
        phase1_start=datetime(2026, 12, 15, tzinfo=timezone.utc),
        phase1_end=datetime(2027, 1, 21, 23, 59, tzinfo=timezone.utc),
        phase1_weekday_rate=150,
        phase1_weekend_rate=225,
        phase2_start=datetime(2027, 1, 22, tzinfo=timezone.utc),
        phase2_end=datetime(2027, 2, 4, 23, 59, tzinfo=timezone.utc),
        phase2_weekday_rate=225,
        phase2_weekend_rate=300,
    )

    assert get_price_by_date(datetime(2026, 12, 14, tzinfo=timezone.utc), setting) == 600
    assert get_price_by_date(datetime(2026, 12, 18, tzinfo=timezone.utc), setting) == 675
    assert get_price_by_date(datetime(2026, 12, 19, tzinfo=timezone.utc), setting) == 712.5
    assert get_price_by_date(datetime(2027, 1, 22, tzinfo=timezone.utc), setting) == 712.5
    assert get_price_by_date(datetime(2027, 1, 23, tzinfo=timezone.utc), setting) == 750


def test_lunar_holiday_event_is_paused_and_not_deducted():
    functions = _load_functions("_process_events")
    functions.update({
        "TZ_TAIPEI": timezone.utc,
        "parse_title": lambda _: {"name": "王小明", "phone": "0912345678", "service": "2人", "note": ""},
        "get_status": lambda _: "已安排",
        "normalize_address": lambda value: value,
        "normalize_phone": lambda value: value,
        "get_weekday_text": lambda _: "二",
        "get_price_by_date": lambda *_: 750,
        "parse_service_people": lambda _: 2,
        "calc_hours": lambda start, end: (end - start).total_seconds() / 3600,
    })
    setting = SimpleNamespace(
        phase1_start=datetime(2026, 12, 15, tzinfo=timezone.utc),
        phase2_end=datetime(2027, 2, 4, 23, 59, tzinfo=timezone.utc),
        lunar_new_year_start=datetime(2027, 2, 5, tzinfo=timezone.utc),
        lunar_new_year_end=datetime(2027, 2, 10, 23, 59, tzinfo=timezone.utc),
    )
    events = [{
        "id": "holiday", "summary": "王小明,0912345678", "location": "台北市", "colorId": "5",
        "start": {"dateTime": "2027-02-09T14:00:00+00:00"},
        "end": {"dateTime": "2027-02-09T17:00:00+00:00"},
    }]

    row = functions["_process_events"](events, "台北", "calendar", [setting])[0]

    assert row["status"] == "暫停"
    assert row["amount"] == ""


def test_schedule_builder_applies_booking_gate_only_when_requested_with_run_period():
    from unittest.mock import patch
    functions = _load_functions("_build_vip_schedule_sheet")
    functions.update({"VIP_SCHEDULE_FALLBACK_HEADERS": [""] * 30, "json": __import__("json")})
    functions["_purchase_item_lookup"] = lambda area: None
    source = SimpleNamespace(get=lambda _range: [["data"]])
    client = SimpleNamespace(open_by_key=lambda _key: SimpleNamespace(worksheet=lambda _name: source))
    result = {"sheet": "台北202612", "count": 1, "differences": {}, "report": "差異", "booking_gate": {"stages": {}}}
    gate = object()
    functions.update({
        "TZ_TAIPEI": timezone.utc,
        "_load_deep_clean_pricing_settings": lambda *_: [SimpleNamespace(
            phase1_start=datetime(2026, 12, 15, tzinfo=timezone.utc),
            phase2_end=datetime(2027, 2, 4, 23, 59, tzinfo=timezone.utc))],
    })
    with patch("tools.service_management.vip_schedule_diff.sync_schedule", return_value=result) as writer, \
            patch("tools.service_management.booking_gate.load_area_gate", return_value=(gate, ["x"])) as loader:
        functions["_build_vip_schedule_sheet"](client, {"name": "台北", "target_spreadsheet_id": "t"}, "202612")
        loader.assert_not_called()
        assert writer.call_args.kwargs["gate"] is None
        functions["_build_vip_schedule_sheet"](client, {"name": "台北", "target_spreadsheet_id": "t"}, "202612",
                                               booking_gate=("2026-12-01", "2027-02-28"))
    assert loader.call_args.args[1:] == ("台北", "2026-12-01", "2027-02-28")
    assert loader.call_args.kwargs["short_periods"] == [("2026-12-15", "2027-02-04")]
    assert writer.call_args.kwargs["gate"] is gate


def test_step3_passes_run_period_when_booking_gate_flag_is_set():
    from datetime import timedelta
    functions = _load_functions("step3_build_vip_schedule_sheets", "_month_keys")
    calls = []
    functions["_build_vip_schedule_sheet"] = (
        lambda gc, area, s, e, compare_only=False, booking_gate=None, sort_rows=False: calls.append(booking_gate) or {})
    tz = timezone(timedelta(hours=8))
    start, end = datetime(2026, 12, 1, tzinfo=tz), datetime(2027, 2, 28, 23, 59, tzinfo=tz)
    functions["step3_build_vip_schedule_sheets"](None, [{"name": "台北"}], start, end)
    functions["step3_build_vip_schedule_sheets"](None, [{"name": "台北"}], start, end, booking_gate=True)
    assert calls == [None, ("2026-12-01", "2027-02-28")]


def _topup_functions():
    functions = _load_functions("normalize_text", "normalize_name_for_compare", "normalize_phone",
                                "_mark_topup_rows", "build_topup_list", "_write_topup_list")
    functions["gspread"] = SimpleNamespace(WorksheetNotFound=KeyError)
    return functions


def _visit(day, amount=1200, status="未安排", name="王小明", balance=3000, note=""):
    month, d = (12, day) if day > 0 else (1, -day)
    year = 2026 if month == 12 else 2027
    return {"name": name, "phone": "0912345678", "address": "台北市", "note": note, "line": "line",
            "status": status, "amount": amount, "balance": balance,
            "start_dt": datetime(year, month, d), "start_str": "09:00"}


def test_topup_marks_only_visits_beyond_balance_and_skips_booked_and_paused():
    mark = _topup_functions()["_mark_topup_rows"]
    rows = [_visit(7), _visit(14, status="已安排"), _visit(21), _visit(28, status="暫停"),
            _visit(-4, 1350), _visit(-18, 1350)]
    mark(rows)
    assert [r["topup"] for r in rows] == ["", "", "", "", "需儲值", "需儲值"]
    enough = [_visit(7, balance=10000), _visit(21, balance=10000)]
    assert [r["topup"] for r in mark(enough)] == ["", ""]


def test_topup_list_groups_customers_with_dates_and_difference():
    functions = _topup_functions()
    rows = functions["_mark_topup_rows"]([
        _visit(7), _visit(21), _visit(-4, 1350, note="每月確認"), _visit(-18, 1350),
        _visit(10, name="李小華", balance=100000),
    ])
    assert functions["build_topup_list"](rows) == [[
        "王小明", "0912345678", "台北市", "每月確認", 2, "01/04、01/18", 5100.0, 3000.0, -2100.0, "line", "", "",
    ]]


def test_topup_list_keeps_manual_notice_columns_and_drops_customers_no_longer_short():
    functions = _topup_functions()

    class Sheet:
        def __init__(self, values):
            self.values, self.updates = values, []

        def get_all_values(self):
            return self.values

        def clear(self):
            self.values = []

        def update(self, values, range_name, **kwargs):
            self.updates.append((range_name, values))
            if range_name == "A1":
                self.values = values

        def freeze(self, **kwargs):
            pass

    header = functions["TOPUP_LIST_HEADERS"]
    old = [header,
           ["王小明", "912345678"] + [""] * 8 + ["已通知", "10/20 LINE"],
           ["陳大同", "0911111111"] + [""] * 8 + ["已通知", ""]]
    sheet = Sheet(old)
    ss = SimpleNamespace(worksheet=lambda name: sheet)
    rows = functions["_mark_topup_rows"]([_visit(7), _visit(21), _visit(-4)])
    functions["_write_topup_list"](ss, "需儲值名單_台北_202612-202702", rows)
    assert len(sheet.values) == 2 and sheet.values[1][0] == "王小明"
    assert sheet.values[1][-2:] == ["已通知", "10/20 LINE"]


def test_topup_list_is_not_created_when_nobody_needs_topup():
    functions = _topup_functions()
    created = []

    def missing(name):
        raise KeyError(name)
    ss = SimpleNamespace(worksheet=missing, add_worksheet=lambda **kw: created.append(kw))
    assert functions["_write_topup_list"](ss, "需儲值名單_台北_202612", [_visit(7, balance=9999)]) == ""
    assert created == []


def test_export_sorts_by_base_name_then_address_then_date():
    functions = _load_functions("normalize_text", "normalize_name_for_compare", "normalize_address",
                                "normalize_phone", "_write_vip_sheet")
    functions["_write_topup_list"] = lambda *args: ""
    writes = []

    class Sheet:
        def clear(self): pass
        def update(self, **kwargs): writes.append(kwargs)
        def freeze(self, **kwargs): pass
        def batch_format(self, formats): pass

    client = SimpleNamespace(open_by_key=lambda _key: SimpleNamespace(worksheet=lambda _name: Sheet()))

    def row(name, address, day):
        return {"service": "", "note": "", "name": name, "phone": "0912345678", "address": address,
                "date_str": f"2026/12/{day:02d}", "start_str": "09:00", "end_str": "12:00", "status": "未安排",
                "weekday": "", "price": 0, "person_hrs": 0, "amount": 0, "subtotal": 0, "balance": 0, "diff": 0,
                "line": "", "start_dt": datetime(2026, 12, day)}

    rows = [row("劉家熙-6樓", "內湖6樓", 1), row("劉家熙-4樓", "內湖4樓", 8), row("劉家熙-4樓", "內湖4樓", 2),
            row("劉家熙-6樓", "內湖6樓", 9), row("王小明", "大安", 5)]
    functions["_write_vip_sheet"](client, "台北", rows, datetime(2026, 12, 1), area_target_id="t")
    out = next(w["values"] for w in writes if w.get("range_name") == "A1")
    assert [(r[2], r[5]) for r in out[1:]] == [
        ("王小明", "2026/12/05"), ("劉家熙-4樓", "2026/12/02"), ("劉家熙-4樓", "2026/12/08"),
        ("劉家熙-6樓", "2026/12/01"), ("劉家熙-6樓", "2026/12/09")]


def test_name_sort_key_orders_surnames_by_taiwan_stroke_count():
    from tools.common.stroke_order import name_sort_key, strokes
    assert [strokes(c) for c in "王李何劉黃蔡陳鄭邱連藍蘇嚴魏"] == [4, 7, 7, 15, 12, 15, 11, 15, 8, 11, 18, 20, 20, 18]
    names = ["黃秀玲", "王小明", "黃煦芬", "劉冠亨", "何雅芳", "李曉微", "台灣大幸", "Amy", "陳靜萱"]
    assert sorted(names, key=name_sort_key) == [
        "王小明", "台灣大幸", "何雅芳", "李曉微", "陳靜萱", "黃煦芬", "黃秀玲", "劉冠亨", "Amy"]


def test_purchase_item_lookup_uses_latest_served_order_at_same_address():
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    import tools.service_management.stored_value as sv
    orders = [
        NS(service_date="2026/09/10", items=["居家清潔"], address="新北市中和區錦和路113號3樓"),
        NS(service_date="2026/10/08", items=["辦公室清潔"], address="新北市中和區錦和路113號3樓"),
        NS(service_date="", items=["儲值金"], address="新北市中和區錦和路113號3樓"),
        NS(service_date="2026/10/20", items=["居家清潔"], address="台北市別的地址"),
    ]
    client = NS(search_orders_by_phone=lambda phone: orders)
    with patch("tools.lemon_backend.client.BackendClient", return_value=client):
        lookup = sv._purchase_item_lookup("台北")
        assert lookup("912025772", "新北市中和區錦和路113號3樓") == "辦公室清潔"
        assert lookup("0912025772", "台北市沒去過") == ""

    def broken(area):
        raise RuntimeError("帳密未設定")
    with patch("tools.lemon_backend.client.BackendClient", side_effect=broken):
        lookup = sv._purchase_item_lookup("台北")
        assert lookup("0912025772", "x") == "" and lookup("0911111111", "y") == ""
