import pandas as pd

from tools.annual_rollover import spring_banquet as sb
from tools.annual_rollover.new_year_generate import Rollover

HEADER = ["姓名", "電話", "email", "生日", "身分證字號", "地址", "到職日期",
          "意外險", "良民證", "總體表現", "薪等", "時薪", "備註", "狀態"]


def frame(*rows):
    return pd.DataFrame([dict(zip(HEADER, r)) for r in rows], columns=HEADER).fillna("")


def row(name, phone, onboard, status):
    return [name, phone, "", "", "", "", onboard, "", "", "", "", "", "", status]


def test_active_rows_keeps_only_normal_and_phone_zero():
    df = frame(row("王小明", "0912345678", "2020-01-01", "正常"),
               row("陳大華", "0922333444", "2019-05-05", "停用"),
               row("林阿美", "", "2021-02-02", "正常"))
    assert sb.active_rows(df) == [
        ["王小明", "'0912345678", "2020-01-01", "正常"],
        ["林阿美", "", "2021-02-02", "正常"],
    ]


def test_run_staff_roster_dry_run_records_failed_area(monkeypatch):
    def fake_export(area, accounts):
        if area == "新竹":
            raise RuntimeError("登入失敗")
        return frame(row("王小明", "0912345678", "2020-01-01", "正常"))

    monkeypatch.setattr(sb, "export_staff_frame", fake_export)
    monkeypatch.setattr("tools.scheduled_monthly.stored_value_settlement.load_accounts", lambda: {})
    rollover = Rollover(None, None, "x", 2027, dry_run=True, log=lambda msg: None)
    rollover.children = lambda folder_id: []

    records = rollover.run("all", names=["春酒專員名冊"])

    assert [r["new_name"] for r in records] == ["2027春酒", "2027春酒專員名冊", "找不到來源"]
    assert records[2]["old_name"] == "專員個資-新竹"
