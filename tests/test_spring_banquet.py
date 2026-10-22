from datetime import date

import pandas as pd

from tools.annual_rollover import spring_banquet as sb
from tools.annual_rollover.new_year_generate import Rollover

HEADER = ["姓名", "電話", "email", "生日", "身分證字號", "地址", "到職日期",
          "意外險", "良民證", "總體表現", "薪等", "時薪", "備註", "狀態"]
FORM = "application/vnd.google-apps.form"


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


def test_attendance_title_replaces_date_and_period():
    title = "問題6.請問您是否方便於03/14(六)中午，參與春酒？"
    assert sb.attendance_title(title, date(2027, 3, 20), "晚上") == \
        "問題6.請問您是否方便於03/20(六)晚上，參與春酒？"
    assert sb.attendance_title("問題1.請問您的中文全名是？", date(2027, 3, 20), "中午") is None


def test_vote_choice_updates_only_name_questions_and_keeps_other():
    form = {"items": [
        {"title": "問題1.請問您已確實閱讀？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "是，我已閱讀完畢"}]}}}},
        {"title": "問題3.您覺得『家事達人獎』，應由誰得獎呢？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "舊人"}, {"isOther": True}]}}}},
    ]}
    reqs = sb.vote_choice_updates(form, ["王小明", "檸檬人1", "王小明", "林阿美"])
    assert len(reqs) == 1
    update = reqs[0]["updateItem"]
    assert update["location"] == {"index": 1}
    assert update["item"]["questionItem"]["question"]["choiceQuestion"]["options"] == [
        {"value": "王小明"}, {"value": "林阿美"}, {"isOther": True}]


def test_run_spring_area_dry_run(monkeypatch):
    def fake_export(area, accounts):
        if area == "新竹":
            raise RuntimeError("登入失敗")
        return frame(row("王小明", "0912345678", "2020-01-01", "正常"))

    prev_files = [{"id": "f1", "name": "2026春酒參與統計表單", "mimeType": FORM},
                  {"id": "f2", "name": "2025年度投票（台北）", "mimeType": FORM},
                  {"id": "f3", "name": "2026春酒參與統計表單 (回覆)", "mimeType": sb.SHEET_MIME}]
    tree = {sb.SPRING_ROOT_FOLDER_ID: [{"id": "prev", "name": "2026春酒",
                                       "mimeType": "application/vnd.google-apps.folder"}],
            "prev": prev_files}

    monkeypatch.setattr(sb, "export_staff_frame", fake_export)
    monkeypatch.setattr("tools.scheduled_monthly.stored_value_settlement.load_accounts", lambda: {})
    monkeypatch.setattr(sb, "get_forms_service", lambda: (_ for _ in ()).throw(AssertionError("不應呼叫")))
    logs = []
    rollover = Rollover(None, None, "x", 2027, dry_run=True, log=logs.append)
    rollover.children = lambda folder_id: tree.get(folder_id, [])
    rollover.spring_event = {"date": date(2027, 3, 13), "period": "中午"}

    records = rollover.run("all", names=["春酒專區"])

    names = [(r["old_name"], r["new_name"]) for r in records]
    assert ("2026春酒參與統計表單", "2027春酒參與統計表單") in names
    assert ("2025年度投票（台北）", "2026年度投票（台北）") in names
    assert ("2025年度投票（台中）", "找不到來源") in names
    assert ("2026春酒參與統計表單 (回覆)", "2027春酒參與統計表單 (回覆)") in names
    assert ("專員個資-新竹", "找不到來源") in names
    assert any("2027春酒專員名冊!台北" in m for m in logs)
