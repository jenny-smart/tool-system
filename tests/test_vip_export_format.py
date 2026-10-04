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
        if isinstance(node, ast.FunctionDef) and node.name in names
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
    with patch("tools.service_management.vip_schedule_diff.sync_schedule", return_value=result) as writer:
        assert functions["_build_vip_schedule_sheet"](
            client, {"name": "台北", "target_spreadsheet_id": "test"}, "202611", compare_only=True
        ) == result
    assert writer.call_args.kwargs["compare_only"] is True
    assert writer.call_args.args[1] == "台北202611"


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
        "normalize_name_for_compare", "normalize_phone", "_write_vip_sheet"
    )

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

    assert [row[2] for row in sheet.updates[0]["values"][1:]] == ["李小華", "王小明"]
    assert sheet.updates[1] == {
        "values": [["0987654321"], ["0912345678"]],
        "range_name": "D2:D3",
        "value_input_option": "RAW",
    }
    assert sheet.formats[0]["range"] == "A2:S200"
    assert [row[18] for row in sheet.updates[0]["values"][1:]] == ["event-li", "event-wang"]
    assert sheet.updates[2]["range_name"] == "T1:U2"
    # Export itself also highlights both active and paused same-day visits.
    duplicate = {**rows[0], "address": "台北市", "event_id": "event-wang-paused", "status": "暫停"}
    rows[0]["address"] = "台北市"
    sheet.updates.clear()
    functions["_write_vip_sheet"](client, "台北", rows + [duplicate], datetime(2026, 10, 1), area_target_id="sheet-id")
    assert [item["range"] for item in sheet.formats[1:]] == ["A3:S3", "A4:S4"]
    assert sheet.updates[0]["values"][3][8] == "暫停"



def test_calendar_export_writes_empty_snapshots_for_each_selected_month():
    from datetime import timedelta
    from unittest.mock import MagicMock
    functions = _load_functions("_month_keys", "step2_export_vip_calendar")
    writer = MagicMock(side_effect=lambda _gc, _area, _rows, start, **kwargs: start.strftime("%Y%m"))
    functions.update({
        "timedelta": timedelta,
        "now_tp": lambda: datetime(2026, 10, 4, tzinfo=timezone.utc),
        "checkin_both": lambda *args: None,
        "_calendar_service": lambda: None,
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
    assert writer.call_count == 2
    assert [call.args[2] for call in writer.call_args_list] == [[], []]
    assert writer.call_args_list[0].kwargs["end_dt"].strftime("%Y%m%d") == "20261130"
    assert writer.call_args_list[1].args[3].strftime("%Y%m%d") == "20261201"
    functions["_load_stored_value_info"].assert_not_called()


def test_same_customer_same_day_events_keep_distinct_ids_and_times():
    functions = _load_functions("_process_events")
    functions.update({
        "TZ_TAIPEI": timezone.utc,
        "parse_title": lambda _: {"name": "王小明", "phone": "0912345678", "service": "1人", "note": ""},
        "get_status": lambda _: "未安排",
        "normalize_address": lambda value: value,
        "normalize_phone": lambda value: value,
        "get_weekday_text": lambda _: "二",
        "get_price_by_date": lambda _: 600,
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
