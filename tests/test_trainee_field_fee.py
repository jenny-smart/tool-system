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
    shixi = [FIELD_HEADER, _field_row("", "王小明", "200", "1.5"), _field_row("", "李大華", "200", "２")]
    fees = field_fees([shijing, shixi])
    assert fees == {"王小明": Decimal("700.0"), "李大華": Decimal(400)}


def test_refund_updates_add_deposit_and_skip_done_or_duplicate_rows():
    refund = [
        ["h"] * 19,
        ["t", "王小明"] + [""] * 17,
        ["t", "李大華", "", "", "", "", "", "", "2026/10/16"],
        ["t", "陳三"] + [""] * 17,
        ["t", "陳三"] + [""] * 17,
        ["t", "黃詠宸", "", "", "", "", "此筆略過(帳號錯誤)", "0"],
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
    from tools.finance_management.trainee_field_fee import roster_info, to_roc_date

    assert to_roc_date("1997/2/4") == "86/02/04"
    assert to_roc_date("2011-1-5") == "100/01/05"
    assert to_roc_date("77/04/29") == "77/04/29"
    roster = roster_info([
        ["時間戳記"],
        ["2025/12/4 上午 2:10", "", "林映竹", "", "1997/2/4", "", "台北市"],
        ["2025/12/9 下午 10:08", "", "廖品婷", "", "881215", "", "新北市汐止區"],
        ["2026/3/9 下午 6:47", "", "莊曉珍", "", "2002/4/9", "", "新北市中和區"],
    ])
    assert roster == {
        "林映竹": ("86/02/04", "台北市"),
        "廖品婷": ("88/12/15", "新北市汐止區"),
        "莊曉珍": ("91/04/09", "新北市中和區"),
    }
    refund = [["h"] * 8, ["t", "莊曉珍", "", "", "", "", "", "2800"]]
    existing = [["姓名"], [], ["莊曉珍", "800", True, "A2", "02/04/09", "新北市"]]
    rows = build_fee_rows(refund, {}, existing, {}, {}, roster)
    assert rows == [["莊曉珍", 800, False, "A2", "91/04/09", "新北市中和區"]]


def test_birthday_formats_unified():
    from tools.finance_management.trainee_field_fee import to_roc_date

    assert to_roc_date("0860912") == "86/09/12"
    assert to_roc_date("20031031") == "92/10/31"
    assert to_roc_date("71.08.07") == "71/08/07"
    assert to_roc_date("民國90年08月20日") == "90/08/20"
    assert to_roc_date("19961/10/08") == ""  # 打錯字留空給人工檢查
    assert to_roc_date("200405/27/") == ""
    assert to_roc_date("") == ""


def test_field_fees_start_from_roster_year():
    from tools.finance_management.trainee_field_fee import (
        field_start_years,
        roster_start_years,
        sum_fees_by_year,
    )

    roster = [["時間戳記"], ["2025/8/6 上午 2:51", "", "林映竹"], ["2026/3/9 下午 6:47", "", "莊曉珍"]]
    start = field_start_years(
        {"林映竹", "莊曉珍", "王小明", "李大華"},
        roster_start_years(roster),
        {"王小明"},
        2026,
    )
    # 名冊建檔年度為起點；名冊沒有的人今年找得到看今年，找不到看前一年
    assert start == {"林映竹": 2025, "莊曉珍": 2026, "王小明": 2026, "李大華": 2025}

    fees_by_year = {
        2025: {"林映竹": Decimal(800), "李大華": Decimal(400), "莊曉珍": Decimal(999)},
        2026: {"林映竹": Decimal(400), "莊曉珍": Decimal(800)},
    }
    assert sum_fees_by_year(start, fees_by_year, 2026) == {
        "林映竹": Decimal(1200),
        "莊曉珍": Decimal(800),
        "王小明": Decimal(0),
        "李大華": Decimal(400),
    }


def test_pick_spreadsheet_prefers_exact_and_skips_disabled():
    from tools.finance_management.trainee_field_fee import pick_spreadsheet

    files = [
        {"id": "old", "name": "2025專員名冊與時數-台北old(停用)"},
        {"id": "new", "name": "2025專員名冊與時數-台北new"},
    ]
    assert pick_spreadsheet("2025專員名冊與時數-台北", files) == "new"
    files.append({"id": "exact", "name": "2025專員名冊與時數-台北"})
    assert pick_spreadsheet("2025專員名冊與時數-台北", files) == "exact"
    assert pick_spreadsheet("2024專員名冊與時數-台北", files) == ""


def test_already_refunded_by_accounting_still_counts():
    """林怡君：I 欄未填，但實境 A 欄已標「已匯款」、押金 B 已歸 0。"""
    shijing = [
        FIELD_HEADER,
        _field_row("2026/10/21已匯款", "林怡君", "200", "2.00"),
        _field_row("2026/10/21已匯款", "林怡君", "200", "2.00"),
        _field_row("2026/10/21已匯款", "林怡君", "200", "2.00"),
        _field_row("202610-1", "林怡君", "200", "2.00"),
    ]
    fees = field_fees([shijing])
    deposits = deposit_lookup([["專員姓名"], ["林怡君", "0", "", "", "退訓", "", "2026/10/21已匯款"]])
    refund = [["h"] * 9, ["t", "林怡君", "", "", "", "", "", "", ""]]
    updates, _ = compute_refund_updates(refund, fees, deposits)
    assert updates == {2: Decimal(3200)}


def test_fee_sheet_append_only_new_names():
    from tools.finance_management.trainee_field_fee import d_formula_for_row, plan_fee_append

    refund = [
        ["h"] * 9,
        ["t", "李政擧", "", "", "", "", "", "3200"],
        ["t", "林怡君", "", "", "", "", "", ""],
        ["t", "張三", "", "", "", "", "", "2000"],
    ]
    fee = [["姓名"], [], ["李政擧", "1200", "TRUE"], ["鄭軫云", "400", "FALSE"], ["", "", "FALSE"]]
    plan = plan_fee_append(refund, {3: Decimal(3200)}, fee)
    assert plan["new_names"] == ["林怡君"]
    assert plan["next_row"] == 5
    assert plan["recheck_rows"] == {"鄭軫云": 4}

    formula = '=ARRAYFORMULA(IF(A3="","",IFERROR(VLOOKUP(A3,IMPORTRANGE("x","y!C:G"),{2,3,5},FALSE),)))'
    assert d_formula_for_row(formula, 23) == '=ARRAYFORMULA(IF(A23="","",IFERROR(VLOOKUP(A23,IMPORTRANGE("x","y!C:G"),2,FALSE),)))'


def test_run_writes_unified_log(monkeypatch):
    from tools.common import unified_log
    from tools.finance_management import trainee_field_fee

    calls = []
    monkeypatch.setattr(unified_log, "write", lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        trainee_field_fee, "_update_refund_and_fee_sheet",
        lambda area: {"h_updated": 0, "fee_rows": 1, "id_checked": 1, "warnings": ["X"]},
    )
    trainee_field_fee.update_refund_and_fee_sheet("台北")
    assert calls[0]["status"] == "成功" and calls[0]["function"] == "新訓退訓實境費用"
    assert "注意：X" in calls[0]["message"]

    def boom(area):
        raise RuntimeError("壞了")

    monkeypatch.setattr(trainee_field_fee, "_update_refund_and_fee_sheet", boom)
    try:
        trainee_field_fee.update_refund_and_fee_sheet("台北")
    except RuntimeError:
        pass
    assert calls[1]["status"] == "失敗" and calls[1]["message"] == "壞了"


def test_mark_refunded_plan():
    from tools.finance_management.trainee_field_fee import (
        normalize_refund_date,
        plan_field_marks,
        plan_mark_refunded,
    )

    assert normalize_refund_date("2026/1/9") == "2026/01/09"
    refund = [
        ["h"] * 9,
        ["t", "林沚罄", "", "", "", "", "", "2000", "2026/10/21"],
        ["t", "林怡君", "", "", "", "", "", "3200", "2026/10/22"],
        ["t", "官志憲", "", "", "", "", "", "2400", "2026/10/16"],  # 押金 G 已註記，略過
        ["t", "張三", "", "", "", "", "", "2000", ""],  # 尚未退款
    ]
    deposit = [
        ["專員姓名"],
        ["林沚罄", "0", "", "", "退訓", "", ""],
        ["林怡君", "0", "", "", "退訓", "", ""],
        ["官志憲", "0", "", "", "退訓", "", "2026/10/16已匯款"],
        ["張三", "2000", "", "", "退訓", "", ""],
    ]
    items, warnings = plan_mark_refunded(refund, deposit)
    assert [(i["name"], i["date"], i["deposit_row"], i["field_amount"]) for i in items] == [
        ("林沚罄", "2026/10/21", 2, Decimal(0)),
        ("林怡君", "2026/10/22", 3, Decimal(1200)),
    ]
    assert warnings == []

    sheets = {"新人實境": [FIELD_HEADER, _field_row("", "林怡君", "200", "2"), _field_row("202610-1", "林怡君", "200", "2"),
                         _field_row("", "林怡君", "200", "2"), _field_row("", "林怡君", "200", "2")]}
    cells, total = plan_field_marks(sheets, "林怡君")
    assert cells == [("新人實境", 2), ("新人實境", 4), ("新人實境", 5)] and total == Decimal(1200)


def test_plan_formula_copies_from_nearest_formula_row():
    from tools.finance_management.trainee_field_fee import plan_formula_copies

    def row(j="", m=""):
        r = [""] * 18
        r[9], r[12] = j, m
        for c in (13, 14, 15, 16, 17):
            r[c] = "=X"
        return r

    values = [["h"] * 18, row("=J", "=M"), row("=J", ""), row("", ""), row("", "=M")]
    copies, warnings = plan_formula_copies(values, [4, 5])
    # 第 4 列：J 取第 3 列、M 往上找到第 2 列；N–R 已有公式不動
    assert (9, 3, 4) in copies and (12, 2, 4) in copies
    # 第 5 列：J 取第 3 列；M 已有公式不動
    assert (9, 3, 5) in copies and all(not (c == 12 and d == 5) for c, _, d in copies)
    assert all(c in (9, 12) for c, _, _ in copies) and warnings == []
