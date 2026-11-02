from tools.service_management.deep_clean_notice import (
    DEEP_CLEAN_BALANCE_HEADERS,
    build_deep_clean_balance_rows,
)


def test_balance_rows_keep_positive_balances_and_requested_columns():
    values = [
        ["客戶姓名", "email", "電話", "LINE@", "剩餘儲值金", "剩餘購物金"],
        ["王小明", "wang@example.com", "0912-345-678", "https://line.me/a", "1,200", "0"],
        ["陳小華", "chen@example.com", "0988-000-000", "https://line.me/b", "0", "500"],
        ["林大同", "lin@example.com", "0977-000-000", "", "0", "0"],
    ]

    rows = build_deep_clean_balance_rows("台北", values)

    assert DEEP_CLEAN_BALANCE_HEADERS == [
        "地區", "客人姓名", "電話", "mail", "LINE連結", "儲值金餘額", "購物金餘額",
    ]
    assert rows == [
        ["台北", "王小明", "0912345678", "wang@example.com", "https://line.me/a", 1200, 0],
        ["台北", "陳小華", "0988000000", "chen@example.com", "https://line.me/b", 0, 500],
    ]


def test_balance_rows_aggregate_multiple_settlement_rows_per_customer():
    values = [
        ["客人姓名", "mail", "手機", "LINE連結", "儲值金餘額", "購物金餘額"],
        ["王小明", "wang@example.com", "0912345678", "line", "100", "20"],
        ["王小明", "wang@example.com", "0912345678", "line", "200", "30"],
    ]

    assert build_deep_clean_balance_rows("台中", values) == [
        ["台中", "王小明", "0912345678", "wang@example.com", "line", 300, 50],
    ]
