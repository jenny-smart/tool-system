from tools.finance_management.taipei_fixed_expense import (
    _already_submitted,
    _next_period,
    parse_zhendan_invoice,
)
from tools.finance_management.taipei_fixed_expense_invoice_helpers import parse_zhongdian_invoice_total


def test_zhendan_uses_pdf_total():
    assert parse_zhendan_invoice("銷售額合計 1,974 營業稅 99 總計 2,073") == (2073, "")


def test_zhongdian_uses_tax_and_service_inclusive_invoice_total():
    body = "下表為8月發票金額，發票金額總計為$92,071，共開立1張發票。"
    assert parse_zhongdian_invoice_total(body) == (
        92071,
        "google廣告費83701＋google服務費8370=92071",
    )


def test_invoice_mail_is_searched_in_following_month_and_existing_memo_is_skipped():
    assert _next_period("202608") == "202609"
    assert _next_period("202612") == "202701"
    assert _already_submitted({"2026.08-眾點，google廣告費83701＋google服務費8370=92071"}, "2026.08-眾點")
