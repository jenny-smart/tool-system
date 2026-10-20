from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from tools.service_management import stored_value


class FakeVipSheet:
    id = 7
    col_count = 22

    def __init__(self, names):
        self.names = names
        self.updates = {}
        self.requests = []
        self.spreadsheet = SimpleNamespace(batch_update=lambda body: self.requests.extend(body["requests"]))

    def get(self, _range):
        return [[n] for n in self.names]

    def resize(self, cols):
        self.col_count = cols

    def update(self, values, range_name, value_input_option):
        self.updates[range_name] = (values, value_input_option)


def test_reply_columns_sync_by_exact_name():
    source = [
        ["地區", "客戶類型", "姓名"] + [""] * 18 + ["回覆狀態", "備註"],
        ["台北", "定期VIP", "劉家熙-4樓"] + [""] * 18 + ["TRUE", "1/20改3人4"],
        ["台北", "定期VIP", "曾偉伶"] + [""] * 18 + ["FALSE", ""],
    ]
    vip = FakeVipSheet(["劉家熙-6樓", "劉家熙-4樓", "劉家熙-4樓", "曾偉伶"])
    office = SimpleNamespace(worksheet=lambda tab: SimpleNamespace(get_all_values=lambda: source))
    target = SimpleNamespace(worksheet=lambda _name: vip)
    gc = SimpleNamespace(open_by_key=lambda key: office if key == "office" else target)
    setting = SimpleNamespace(season_year=2026, phase1_start=datetime(2026, 12, 15), phase2_end=datetime(2027, 2, 4))

    with patch("tools.common.year_roots.root_file_id", return_value="office") as locate:
        count = stored_value._sync_deep_clean_replies(
            gc, "台北", "target", "定期VIP_台北_202612-202702", [setting],
            datetime(2026, 12, 1), datetime(2027, 2, 28),
        )

    assert count == 3
    locate.assert_called_once_with("內勤工作表單", "台北", year=2026)
    assert vip.col_count == 23
    assert vip.updates["V1:V5"] == ([["回覆狀態"], [""], ["TRUE"], ["TRUE"], ["FALSE"]], "USER_ENTERED")
    assert vip.updates["W1:W5"] == ([["備註"], [""], ["1/20改3人4"], ["1/20改3人4"], [""]], "RAW")
    checkbox = [r["setDataValidation"]["range"] for r in vip.requests if "rule" in r["setDataValidation"]]
    assert [(r["startRowIndex"], r["endRowIndex"]) for r in checkbox] == [(2, 5)]


def test_reply_sync_skips_seasons_outside_export_range():
    setting = SimpleNamespace(season_year=2026, phase1_start=datetime(2026, 12, 15), phase2_end=datetime(2027, 2, 4))
    with patch("tools.common.year_roots.root_file_id") as locate:
        assert stored_value._sync_deep_clean_replies(
            SimpleNamespace(), "台北", "target", "定期VIP_台北_202610", [setting],
            datetime(2026, 10, 1), datetime(2026, 10, 31),
        ) == 0
    locate.assert_not_called()
