from decimal import Decimal

from tools.finance_management.trainee_field_fee import (
    build_fee_rows,
    compute_refund_updates,
    deposit_lookup,
    field_fees,
)

FIELD_HEADER = ["A"] * 11


def _field_row(paid, name, fee, hours):
    return [paid, "2026/10/02", name, "", "", "", "", "", "", fee, hours]


def test_field_fees_sum_both_sheets_and_skip_paid_rows():
    shijing = [FIELD_HEADER, _field_row("", "王小明", "200", "2.00"), _field_row("202610-1", "王小明", "200", "2")]
    shixi = [FIELD_HEADER, _field_row("", "王小明", "200", "1.5"), _field_row("", "李大華", "200", "2")]
    fees = field_fees([shijing, shixi])
    assert fees == {"王小明": Decimal("700.0"), "李大華": Decimal(400)}


def test_refund_updates_add_deposit_and_skip_done_or_duplicate_rows():
    refund = [
        ["h"] * 19,
        ["t", "王小明"] + [""] * 17,
        ["t", "李大華", "", "", "", "", "", "", "2026/10/16"],
        ["t", "陳三"] + [""] * 17,
        ["t", "陳三"] + [""] * 17,
    ]
    deposits = deposit_lookup([["專員姓名"], ["王小明", "2000"], ["李大華", "2000"], ["陳三", "2000"]])
    updates, warnings = compute_refund_updates(refund, {"王小明": Decimal(800)}, deposits)
    assert updates == {2: Decimal(2800)}
    assert len(warnings) == 1 and "陳三" in warnings[0]


def test_fee_rows_filter_over_2000_and_keep_existing_info():
    refund = [["h"] * 8, ["t", "王小明", "", "", "", "", "", "3200"], ["t", "李大華", "", "", "", "", "", "2000"]]
    existing = [["姓名"], [], ["王小明", "1200", True, "F1", "65/10/28", "台北市"]]
    rows = build_fee_rows(refund, {}, existing, {}, {"王小明": True})
    assert rows == [["王小明", 1200, True, "F1", "65/10/28", "台北市"]]


def test_birthday_converted_to_roc_and_overrides_existing():
    from tools.finance_management.trainee_field_fee import roster_birthdays, to_roc_date

    assert to_roc_date("1997/2/4") == "86/02/04"
    assert to_roc_date("2011-1-5") == "100/01/05"
    assert to_roc_date("77/04/29") == "77/04/29"
    roster = roster_birthdays([
        ["時間戳記"],
        ["2025/12/4 上午 2:10", "", "林映竹", "", "1997/2/4"],
        ["2026/3/9 下午 6:47", "", "莊曉珍", "", "2002/4/9"],
    ])
    assert roster == {"林映竹": "1997/2/4", "莊曉珍": "91/04/09"}
    refund = [["h"] * 8, ["t", "莊曉珍", "", "", "", "", "", "2800"]]
    existing = [["姓名"], [], ["莊曉珍", "800", True, "A2", "02/04/09", "新北市"]]
    rows = build_fee_rows(refund, {}, existing, {}, {}, roster)
    assert rows == [["莊曉珍", 800, False, "A2", "91/04/09", "新北市"]]
