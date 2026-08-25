from tools.newebpay.express_payment_data import ExpressPayment, amount_value, new_rows


def _payment(transaction_no: str, amount: int = 5400) -> ExpressPayment:
    return ExpressPayment(
        merchant="MS3205758733\n檸檬專業清潔LemonClean",
        transaction_no=transaction_no,
        merchant_order_no="20240827095136Etkb4l07kQ",
        product="檸檬家事服務單次$1800",
        payment_method="信用卡",
        status_and_time="付款成功\n2026-08-25 12:04:11",
        payer="劉忻恬",
        phone="0916718665",
        amount=amount,
    )


def test_amount_value_keeps_numeric_amount() -> None:
    assert amount_value("NT$ 5,400 含運費：NT$ 0") == 5400
    assert amount_value("NT$ 1,800.50") == 1800.5


def test_new_rows_skips_existing_and_duplicate_transactions() -> None:
    existing = "26072709300281231"
    fresh = "26082512041169827"
    rows = new_rows(
        [_payment(existing), _payment(fresh), _payment(fresh)],
        {existing},
    )

    assert len(rows) == 1
    assert rows[0][1] == f"{fresh}\n20240827095136Etkb4l07kQ"
    assert rows[0][6] == "0916718665"
    assert rows[0][7] == 5400
