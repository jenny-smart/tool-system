import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "tools/bank_statement/fubon_supply_purchase_filter.py"
SPEC = importlib.util.spec_from_file_location("fubon_supply_purchase_filter", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
pending_supply_purchases = MODULE.pending_supply_purchases


def _row(
    *,
    month="2026/10",
    supplier="LINE酵素",
    amount="2310",
    bank="807(永豐銀行)",
    account="11601800014602",
    payment_date="",
    request_date="",
):
    row = [""] * 19
    row[1] = month
    row[5] = supplier
    row[10] = amount
    row[13] = bank
    row[14] = account
    row[16] = payment_date
    row[18] = request_date
    return row


def test_paid_row_is_not_returned_to_supply_purchase_list():
    values = [[""] * 17, _row(payment_date="2026/10/13")]

    assert pending_supply_purchases(values, "202610") == []


def test_group_only_contains_and_sums_rows_without_payment_date():
    values = [
        [""] * 17,
        _row(amount="2310", payment_date="2026/10/13"),
        _row(amount="1200"),
        _row(amount="800"),
    ]

    assert pending_supply_purchases(values, "202610") == [
        {
            "sheet_row": 3,
            "rows": [3, 4],
            "supplier": "LINE酵素",
            "bank_code": "807",
            "account_number": "11601800014602",
            "amount": "2000",
        }
    ]


def test_request_date_in_s_means_paid_and_na_account_is_skipped():
    values = [[""] * 19, _row(request_date="2026/02/10"), _row(bank="#N/A", account="#N/A"), _row(amount="500")]

    result = pending_supply_purchases(values, "202610")
    assert [item["rows"] for item in result] == [[4]]
