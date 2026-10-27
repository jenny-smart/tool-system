"""工具包押金退款盤點：H 押金＞0、I 欄有日期、S 欄空白才列入。"""
from tools.bank_statement.fubon_deposit_refund_filter import has_date, pending_deposit_refunds


def _row(deposit="2000", i="2026/10/20", memo="王小明新訓", amount="2000", s=""):
    row = [""] * 19
    row[7], row[8], row[16], row[17], row[18] = deposit, i, memo, amount, s
    return row


def test_only_rows_with_date_in_column_i():
    values = [["header"], _row(), _row(i=""), _row(i="離職"), _row(i="2026-10-21"), _row(i="10/22"),
              _row(s="2026/10/25"), _row(deposit="0")]
    assert [r["sheet_row"] for r in pending_deposit_refunds(values)] == [2, 5, 6]


def test_has_date_formats():
    assert all(map(has_date, ["2026/10/20", "2026-10-20", "2026.10.20", "10/20", "2026年10月20日", "2026/10/20 14:00"]))
    assert not any(map(has_date, ["", "離職", "待確認", "2000"]))
