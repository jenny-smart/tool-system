from tools.finance_management.statement_rules import Rule, _split_integer_amount


def _rule(split_value, action="更新原列", offset=0):
    return Rule([
        True, "測試", "D", "等於/包含", "台北記帳費",
        "", "", "", "執行業務", offset, "記帳費", False,
        action, split_value, "", "", "",
    ])


def test_numeric_split_parts_are_parsed_without_breaking_legacy_column_mode():
    numeric = _rule(6)
    assert numeric.split_parts == 6
    assert numeric.split_column == ""

    legacy = _rule("E")
    assert legacy.split_parts is None
    assert legacy.split_column == "E"


def test_six_way_integer_split_keeps_exact_total_and_remainder():
    shares = _split_integer_amount(10000, 6)
    assert shares == [1666.0, 1666.0, 1667.0, 1667.0, 1667.0, 1667.0]
    assert sum(shares) == 10000


def test_two_way_odd_split_keeps_exact_total():
    shares = _split_integer_amount(10001, 2)
    assert shares == [5000.0, 5001.0]
    assert sum(shares) == 10001


def test_blank_month_source_prefers_manual_j_month_marker():
    rule = _rule(6, offset=-5)
    row = ["", "", "", "台北記帳費", 10000, "", "", "", "", "202606台北記帳費", "2026/09/02"]
    assert rule.l_value(row) == "2026.01-記帳費"
