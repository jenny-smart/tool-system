import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


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

        def sort(self, *args, **kwargs):
            self.sort_args = (args, kwargs)

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
        {**base, "name": "王小明", "phone": "912345678"},
        {**base, "name": "李小華", "phone": "0987654321"},
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
    assert sheet.sort_args == (((3, "asc"),), {"range": "A2:R3"})
