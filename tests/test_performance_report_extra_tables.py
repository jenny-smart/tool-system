import pandas as pd

from tools.scheduled_daily import performance_report as report


def test_configurable_month_ranges_cross_year():
    assert report.get_report_month_ranges("2026-11", "2027-02") == [
        ("2026/11", "2026-11-01", "2026-11-30"),
        ("2026/12", "2026-12-01", "2026-12-31"),
        ("2027/01", "2027-01-01", "2027-01-31"),
        ("2027/02", "2027-02-01", "2027-02-28"),
    ]


def test_order_date_summary_groups_region_and_splits_payment():
    records = [
        {"__city": "台北", "created_at": "2026-08-17 10:00:00", "total": "1,000", "purchase_status": "0"},
        {"__city": "台北", "created_at": "2026-08-18 10:00:00", "total": "2,000", "purchase_status": "1"},
        {"__city": "台中", "created_at": "2026-08-18 11:00:00", "total": 500, "purchase_status": "0"},
        {"__city": "台北", "created_at": "2026-08-18 12:00:00", "total": 999, "purchase_status": "1", "cancel_at": "2026-08-18"},
    ]
    out = report.build_order_date_summary(records)
    assert out.iloc[0].to_dict() == {
        "地區": "台北", "未付款": 1000,
        "已付款": 2000, "未付款＋已付款": 3000,
    }
    assert out.iloc[1]["地區"] == "台中"
    assert out.iloc[-1].to_dict() == {
        "地區": "加總", "未付款": 1500,
        "已付款": 2000, "未付款＋已付款": 3500,
    }


def test_reserve_hours_and_net_performance():
    month_ranges = [
        ("2026/08", "2026-08-01", "2026-08-31"),
        ("2026/09", "2026-09-01", "2026-09-30"),
    ]
    reserve_records = [
        {
            "__city": "台北", "order_no": "LC1", "date_clean": "2026-08-20",
            "name": "檸檬保留", "person": 2, "hour": 4, "total": 4800,
        },
        {
            "__city": "台中", "order_no": "LC2", "date_clean": "2026-08-21",
            "notice": "大掃除檸檬保留單 reserve_1", "person": 2,
            "period_s": "09:00", "period_e": "12:00", "total": 3600,
        },
    ]
    reserve_df = report.build_reserve_summary(reserve_records, month_ranges)
    taipei = reserve_df[reserve_df["地區"] == "台北"].iloc[0]
    taichung = reserve_df[reserve_df["地區"] == "台中"].iloc[0]
    assert taipei["2026/08保留單時數"] == 8
    assert taipei["2026/08保留單業績"] == 4800
    assert taichung["2026/08保留單時數"] == 6

    raw_df = pd.DataFrame([
        {"城市": "台北", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 2000},
        {"城市": "台中", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 0},
    ])
    net_df = report.build_net_performance_summary(raw_df, reserve_df, month_ranges)
    performance_df = report.build_month_performance_summary(raw_df, month_ranges)
    taipei_net = net_df[net_df["地區"] == "台北"].iloc[0]
    taichung_net = net_df[net_df["地區"] == "台中"].iloc[0]
    assert taipei_net["2026/08業績－保留單業績"] == 5200
    assert taichung_net["2026/08業績－保留單業績"] == 4400
    assert performance_df[performance_df["地區"] == "加總"].iloc[0]["2026/08業績"] == 18000

    for city in [*report.CITY_ORDER, "加總"]:
        gross = performance_df[performance_df["地區"] == city].iloc[0]["2026/08業績"]
        reserve = reserve_df[reserve_df["地區"] == city].iloc[0]["2026/08保留單業績"]
        net = net_df[net_df["地區"] == city].iloc[0]["2026/08業績－保留單業績"]
        assert gross - reserve == net
