from tools.invoice_center.allowance_filter import pending_allowances


def test_pending_allowances_includes_preview_details():
    header = [""] * 28
    row = [""] * 28
    row[1] = "待退款"
    row[6] = "LC00123456"
    row[7] = "王小明"
    row[10] = "客戶取消服務"
    row[18] = "1,400"
    row[23] = "FP12345678"
    row[25] = "1333.333"
    row[27] = ""

    result = pending_allowances([header, row])

    assert result == [{
        "sheet_row": 2,
        "order_no": "LC00123456",
        "customer": "王小明",
        "reason": "客戶取消服務",
        "invoice_no": "FP12345678",
        "refund_amount": "1400",
        "untaxed_amount": "1333.333",
    }]


def test_pending_allowances_keeps_existing_filters():
    header = [""] * 28
    row = [""] * 28
    row[1] = "待退款"
    row[6] = "LC00123456"
    row[7] = "王小明"
    row[10] = "客戶取消服務"
    row[18] = "1,400"
    row[23] = "FP12345678"
    row[25] = "1333.333"
    row[27] = "AL12345678"

    assert pending_allowances([header, row]) == []
