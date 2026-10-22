import re
from datetime import date

import pandas as pd

from tools.annual_rollover import spring_banquet as sb
from tools.annual_rollover.new_year_generate import Rollover, col_index

HEADER = ["姓名", "電話", "email", "生日", "身分證字號", "地址", "到職日期",
          "意外險", "良民證", "總體表現", "薪等", "時薪", "備註", "狀態"]
FORM = "application/vnd.google-apps.form"


def frame(*rows):
    return pd.DataFrame([dict(zip(HEADER, r)) for r in rows], columns=HEADER).fillna("")


def row(name, phone, onboard, status):
    return [name, phone, "", "", "", "", onboard, "", "", "", "", "", "", status]


def test_active_rows_keeps_normal_drops_test_accounts():
    df = frame(row("王小明", "0912345678", "2020-01-01", "正常"),
               row("陳大華", "0922333444", "2019-05-05", "停用"),
               row("檸檬人1", "0900000000", "2020-01-01", "正常"),
               row("林阿美", "", "2021-02-02", "正常"))
    assert sb.active_rows(df) == [
        ["王小明", "'0912345678", "2020-01-01", "正常"],
        ["林阿美", "", "2021-02-02", "正常"],
    ]


def test_parse_hours_both_layouts():
    taipei = [[" ", "外勤", "月份", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"],
              ["", "", "給班人數", "5,844"],
              ["1", "蔡立娟", "5星", "174", "108", "116", "180", "136", "168", "168", "152", "168", "164", "160", "140"]]
    taoyuan = [["", "在職", "外勤", "月份", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月",
                "11月", "12月"],
               ["1", "是", "張維翰", "5星", "", "8", "1,048"]]
    assert sb.parse_hours(taipei)["蔡立娟"][:3] == [174, 108, 116]
    assert sb.parse_hours(taoyuan)["張維翰"][:3] == [0, 8, 1048]
    assert len(sb.parse_hours(taoyuan)["張維翰"]) == 12


def test_roster_rows_formulas_use_threshold_cell():
    rows = sb.roster_rows("台北", [["王小明", "'0912", "2020-01-01", "正常"]], {"王小明": [10] * 12}, 2)
    assert rows[0][:6] == ["台北", "王小明", "'0912", "2020-01-01", "正常", 10]
    assert rows[0][17:] == ["=SUM(F2:Q2)", "=COUNTIF(F2:Q2,\"<>0\")", "=IFERROR(R2/S2,0)",
                            "=IF(T2>$X$1,\"Y\",\"\")"]


def test_prelim_rows_replaces_staff_keeps_inner():
    existing = [["台北專員", "舊人"], ["台北內勤", "張齊恩"], ["桃園專員", "桃園舊人"], ["桃園內勤", "沈宛茹"]]
    rows = sb.prelim_rows(existing, {"台北": ["王小明", "張齊恩"]})
    assert rows == [["台北專員", "王小明"], ["台北內勤", "張齊恩"], ["桃園專員", "桃園舊人"], ["桃園內勤", "沈宛茹"]]
    formulas = sb.prelim_formulas(rows, "2027春酒專員名冊")
    assert formulas[0][9].startswith("=IFERROR(XLOOKUP($B2,'2027春酒專員名冊'!$B:$B")
    assert formulas[1][9:] == ["", "='2027春酒專員名冊'!$X$1"]


def test_attendance_title_replaces_date_and_period():
    title = "問題6.請問您是否方便於03/14(六)中午，參與春酒？"
    assert sb.attendance_title(title, date(2027, 3, 20), "晚上") == \
        "問題6.請問您是否方便於03/20(六)晚上，參與春酒？"
    assert sb.attendance_title("問題1.請問您的中文全名是？", date(2027, 3, 20), "中午") is None


def test_vote_choice_updates_excludes_recent_winner_of_same_award():
    form = {"items": [
        {"title": "問題1.請問您已確實閱讀？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "是，我已閱讀完畢"}]}}}},
        {"title": "問題3.您覺得『家事達人獎』，應由誰得獎呢？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "舊人"}, {"isOther": True}]}}}},
        {"title": "問題4.您覺得『最佳進步獎』，應由誰得獎呢？", "questionItem": {"question": {"choiceQuestion": {
            "options": []}}}},
    ]}
    reqs = sb.vote_choice_updates(form, ["王小明", "檸檬人1", "王小明", "林阿美"],
                                  {"家事達人獎": {"王小明"}})
    assert [r["updateItem"]["location"]["index"] for r in reqs] == [1, 2]
    opts = [r["updateItem"]["item"]["questionItem"]["question"]["choiceQuestion"]["options"] for r in reqs]
    assert opts[0] == [{"value": "林阿美"}, {"isOther": True}]
    assert opts[1] == [{"value": "王小明"}, {"value": "林阿美"}]


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
    assert any("台北：狀態「正常」1 人" in m for m in logs)


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheets:
    """只支援春酒專區用到的 Sheets 呼叫；values 以 {工作表: {(列,欄): 值}} 存放。"""

    def __init__(self, data):
        self.data = data
        self.cleared, self.appended, self.requests = [], [], []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def _sheet(self, a1):
        return a1.split("!")[0].strip("'")

    def get(self, spreadsheetId, fields=None, **kw):
        a1 = kw.get("range")
        if a1 is None:
            return _Call(lambda: {"sheets": [{"properties": {"title": t, "sheetId": i}}
                                             for i, t in enumerate(self.data)]})
        sheet = self._sheet(a1)
        cells = self.data.get(sheet, {})
        if not cells:
            return _Call(lambda: {})
        start = re.match(r"([A-Z]+)(\d+)", a1.split("!")[1]).groups()
        r0, c0 = int(start[1]), col_index(start[0])
        rows = sorted({r for r, _ in cells if r >= r0})
        return _Call(lambda: {"values": [[cells.get((r, c), "") for c in range(c0, max(c for _, c in cells) + 1)]
                                         for r in rows]})

    def batchClear(self, spreadsheetId, body):
        self.cleared += body["ranges"]
        return _Call(lambda: {})

    def update(self, spreadsheetId, valueInputOption, body, **kw):
        return _Call(lambda: {})

    def append(self, spreadsheetId, valueInputOption, insertDataOption, body, **kw):
        self.appended += body["values"]
        return _Call(lambda: {})

    def batchUpdate(self, spreadsheetId, body):
        self.requests += body.get("requests", []) + body.get("data", [])
        for r in body.get("requests", []):
            if "addSheet" in r:
                self.data.setdefault(r["addSheet"]["properties"]["title"], {})
        return _Call(lambda: {})


def cells(rows, start_row=1):
    return {(start_row + i, c): v for i, row in enumerate(rows) for c, v in enumerate(row)}


def test_prepare_and_update_roster_with_fake_sheets(monkeypatch):
    data = {
        "統計": cells([["", "『家事達人獎』", "『最佳進步獎』"], ["台北", "楊超顯", "余世烽"], ["", "何語婷"]]),
        "2026春酒專員名冊": {},
        "初步統計": cells([["", ""], ["台北專員", "舊人"], ["台北內勤", "張齊恩"]]),
        "春酒參與統計": {}, "活動流程": {}, "工作分配": {},
    }
    fake = FakeSheets(data)
    rollover = Rollover(None, fake, "x", 2027, dry_run=False, log=lambda m: None)
    rollover.find_file_by_name = lambda name: None

    sb.prepare_response_file(rollover, "resp", {"date": date(2027, 3, 13)})
    assert ["'2025", "台北", "家事達人獎", "何語婷"] in fake.appended
    assert any(r.get("updateSheetProperties", {}).get("properties", {}).get("title") == "2027春酒專員名冊"
               for r in fake.requests if isinstance(r, dict))
    assert any(d.get("range") == "'活動流程'!A3" and d["values"][0][0] == "日期：2027年03月13日(六)"
               for d in fake.requests if isinstance(d, dict) and "range" in d)

    data["2027春酒專員名冊"] = data.pop("2026春酒專員名冊")
    sb.update_roster(rollover, "resp", {"台北": [["王小明", "'0912", "2020-01-01", "正常"],
                                               ["張齊恩", "'0911", "2020-01-01", "正常"]]})
    prelim = next(d for d in fake.requests if isinstance(d, dict) and d.get("range") == "'初步統計'!A2")
    assert [r[:2] for r in prelim["values"]] == [["台北專員", "王小明"], ["台北內勤", "張齊恩"]]


def test_manual_columns_follow_person_after_reorder():
    existing = [["台北專員", "舊人", "", "", "", "", "1", "", "2", "", "", "Y"],
                ["台北內勤", "張齊恩", "", "", "", "", "", "3"]]
    manual = sb.manual_by_name(existing)
    assert manual[("台北專員", "舊人")] == ["1", "", "2", "Y"]
    assert manual[("台北內勤", "張齊恩")] == ["", "3", "", ""]


def test_fresh_single_area_is_rejected():
    logs = []
    rollover = Rollover(None, None, "x", 2027, dry_run=True, fresh=True, log=logs.append)
    rollover.children = lambda folder_id: (_ for _ in ()).throw(AssertionError("不應動到資料夾"))
    assert sb.run_spring_area(rollover, "台北") == []
    assert any("只能選全區" in m for m in logs)


def test_one_form_failure_does_not_stop_other_regions(monkeypatch):
    FOLDER = "application/vnd.google-apps.folder"
    prev_files = [{"id": f"v{a}", "name": f"2025年度投票（{a}）", "mimeType": FORM} for a in sb.ROSTER_AREAS]
    tree = {sb.SPRING_ROOT_FOLDER_ID: [{"id": "prev", "name": "2026春酒", "mimeType": FOLDER},
                                       {"id": "cur", "name": "2027春酒", "mimeType": FOLDER}],
            "prev": prev_files,
            "cur": [{"id": f"n{a}", "name": f"2026年度投票（{a}）", "mimeType": FORM} for a in sb.ROSTER_AREAS]}
    monkeypatch.setattr(sb, "export_staff_frame",
                        lambda area, acc: frame(row(f"{area}人", "0912", "2020-01-01", "正常")))
    monkeypatch.setattr("tools.scheduled_monthly.stored_value_settlement.load_accounts", lambda: {})
    monkeypatch.setattr(sb, "get_forms_service", lambda: object())
    updated = []

    def fake_update(rollover, forms, form_id, form_name, names, excluded):
        if "台北" in form_name:
            raise RuntimeError("權限不足")
        updated.append(form_name)

    monkeypatch.setattr(sb, "update_vote_form", fake_update)
    rollover = Rollover(None, None, "x", 2027, dry_run=True, log=lambda m: None)
    rollover.children = lambda folder_id: tree.get(folder_id, [])
    records = sb.run_spring_area(rollover)
    assert updated == ["2026年度投票（台中）", "2026年度投票（桃園）", "2026年度投票（新竹）"]
    assert {"kind": "表單", "old_name": "2026年度投票（台北）", "old_id": "", "new_name": "找不到來源",
            "new_id": ""} in records
