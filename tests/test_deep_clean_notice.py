from datetime import datetime, timedelta, timezone

from tools.service_management.deep_clean_notice import (
    _extra_charge,
    build_nonroutine_notice,
    build_notice_rows,
)


TZ = timezone(timedelta(hours=8))


def _row(day, name="王小明", person_hrs=6):
    start = datetime.fromisoformat(day).replace(hour=9, tzinfo=TZ)
    return {
        "start_dt": start,
        "date_str": start.strftime("%Y/%m/%d"),
        "weekday": "一",
        "start_str": "09:00",
        "end_str": "12:00",
        "service": "2人",
        "person_hrs": person_hrs,
        "name": name,
        "phone": "0912345678",
        "address": "台北市測試路1號",
    }


def test_extra_charge_uses_weekend_rate():
    weekday = _row("2026-12-18")
    weekend = _row("2026-12-19")
    assert _extra_charge(weekday, 100, 250) == 300
    assert _extra_charge(weekend, 100, 250) == 750


def test_regular_notice_contains_mail_merge_fields_and_next_service():
    rows = [_row("2026-12-18"), _row("2027-01-23"), _row("2027-02-12")]
    output = build_notice_rows(
        "台北",
        rows,
        {"王小明": {"lineValue": "https://chat.line.biz/example", "email": "vip@example.com"}},
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, 23, 59, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, 23, 59, tzinfo=TZ),
        100,
        250,
        200,
        300,
        "2026/11/03（二）17:00",
    )
    assert len(output) == 1
    row = output[0]
    assert row[1] == "定期VIP"
    assert row[4] == "vip@example.com"
    assert "2027/02/12" in row[9]
    assert row[16] == "待寄送"
    assert "如改期，將依實際服務日期重新計算" in row[15]


def test_nonroutine_notice_uses_open_window_and_correct_customer_label():
    notice = build_nonroutine_notice(
        "測試客戶",
        datetime(2026, 12, 15, tzinfo=TZ),
        datetime(2027, 1, 21, tzinfo=TZ),
        datetime(2027, 1, 22, tzinfo=TZ),
        datetime(2027, 2, 4, tzinfo=TZ),
        100,
        250,
        200,
        300,
        datetime(2026, 11, 5, tzinfo=TZ),
        datetime(2026, 11, 10, tzinfo=TZ),
    )
    assert "《VIP 客戶年節大掃除加價收費說明》" in notice
    assert "VIP 開放預約時間：2026/11/05～2026/11/10" in notice
    assert "VIP 定期客戶" not in notice
