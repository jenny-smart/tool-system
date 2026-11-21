from tools.field_management import staff_orders as so

H = ["", "訂單編號", "服務日期", "服務時間", "服務時數", "客戶姓名", "聯絡電話", "服務專員", "購買項目", "取消狀態"]


def row(no, d, staff="甲 X 乙", t="09:00-12:00"):
    return ["", no, d, t, "3", "王", "0912345678", staff, "1專業清潔", ""]


def test_window_and_previous_file_dates():
    assert so.date_window("20261202") == ("2026/11/01", "2026/12/02")
    assert so.date_window("20260105") == ("2025/12/01", "2026/01/05")
    assert so.previous_month_file_dates("20261202")[:2] == ["20261130", "20261129"]


def test_only_up_to_today_and_previous_month_from_prev_file():
    this_month = [H, row("A", "2026/12/1"), row("B", "2026/12/2"), row("C", "2026/12/3"),
                  row("Z", "2026-11-30", staff="舊")]
    prev = [H, row("X", "2026/11/1"), row("Y", "2026/11/30", staff="丙"), row("Q", "2026/10/31")]
    out = so.build_staff_orders(this_month, prev, "20261202")
    assert out[0] == so.FIELDS
    keys = [(r[0], r[1]) for r in out[1:]]
    assert keys == [("X", "2026/11/01"), ("Y", "2026/11/30"), ("Z", "2026/11/30"),
                    ("A", "2026/12/01"), ("B", "2026/12/02")]
    assert ("C", "2026/12/03") not in keys            # 明天以後不列
    assert ("Q", "2026/10/31") not in keys            # 上上個月不列


def test_missing_previous_file_lists_this_month_only():
    out = so.build_staff_orders([H, row("A", "2026/12/1")], None, "20261202")
    assert [r[0] for r in out[1:]] == ["A"]


def test_fields_mapped_by_header_name():
    out = so.build_staff_orders([H, row("A", "2026/12/1", staff="甲 X 乙")], None, "20261202")
    rec = dict(zip(out[0], out[1]))
    assert rec["服務專員"] == "甲 X 乙" and rec["服務時數"] == "3" and rec["服務細項"] == ""
