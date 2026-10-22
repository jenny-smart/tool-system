import re
from datetime import date

import pandas as pd

from tools.annual_rollover import spring_banquet as sb
from tools.annual_rollover.new_year_generate import Rollover, col_index

HEADER = ["姓名", "電話", "email", "生日", "身分證字號", "地址", "到職日期",
          "意外險", "良民證", "總體表現", "薪等", "時薪", "備註", "狀態"]
FORM, SHEET, FOLDER = sb.FORM_MIME, sb.SHEET_MIME, sb.FOLDER_MIME


def frame(*rows):
    return pd.DataFrame([dict(zip(HEADER, r)) for r in rows], columns=HEADER).fillna("")


def row(name, phone, onboard, status):
    return [name, phone, "", "", "", "", onboard, "", "", "", "", "", "", status]


def test_active_rows_drops_test_and_south_accounts():
    df = frame(row("王小明", "0912345678", "2020-01-01", "正常"),
               row("陳大華", "0922333444", "2019-05-05", "停用"),
               row("南區檸檬人1", "0900000000", "2020-01-01", "正常"),
               row("李憶綾(高雄)", "0900000001", "2020-01-01", "正常"),
               row("蘇信文(台南)", "0900000002", "2020-01-01", "正常"),
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


def test_roster_rows_threshold_and_award_columns():
    rows = sb.roster_rows("台北", [["王小明", "'0912", "2020-01-01", "正常"]], {"王小明": [10] * 12}, 2, 2027)
    assert rows[0][:6] == ["台北", "王小明", "'0912", "2020-01-01", "正常", 10]
    assert rows[0][20] == "=IF(T2>$AB$1,\"Y\",\"\")"
    assert "未回覆" in rows[0][23] and "投票回應-" in rows[0][24]
    assert "=$B2" in rows[0][21] and "\"2025\"" in rows[0][21] and "\"2024\"" in rows[0][22]


def test_attend_order_puts_inner_last_without_duplicates():
    roster = [["台北", "王小明"], ["台北", "張齊恩"], ["桃園", "沈宛茹"]]
    inner = [["台北/台中內勤", "張齊恩"], ["桃園內勤", "沈宛茹"]]
    assert sb.attend_order(roster, inner) == ["王小明", "張齊恩", "沈宛茹"]


def test_stats_formulas_payment_year_and_top5():
    person = sb.stats_person_formulas(2027)
    assert person["A2"][0][0].startswith("={FILTER('2027春酒專員名冊'!A2:B")
    assert person["L2"][0][0] == '=IF(IFERROR($D2>=DATE(2027,1,1),FALSE),"Y","")'
    assert len(person["C2"]) == sb.STATS_ROWS - 1
    summary = sb.stats_summary_formulas(2027)
    assert summary["Q1"][1][9] == "2027到職本人付費"
    assert ",5,2)" in summary["Q19"][1][1] and "投票回應-台北" in summary["Q19"][1][1]
    assert "Q40" in summary                      # 第 4 區（台中）區塊
    others = sb.other_formulas()
    assert "'春酒統計表'!E2:E300=\"是\"" in others["'眷屬收費'!A2"][0][0]


def test_attendance_title_and_description():
    title = "問題6.請問您是否方便於03/14(六)中午，參與春酒？"
    assert sb.attendance_title(title, date(2027, 3, 20), "晚上") == \
        "問題6.請問您是否方便於03/20(六)晚上，參與春酒？"
    desc = "親愛的各位夥伴們\n春酒時間：2026/03/14(六)-中午\n其他"
    assert sb.attendance_description(desc, date(2027, 3, 20), "晚上") == \
        "親愛的各位夥伴們\n春酒時間：2027/03/20(六)-晚上\n其他"
    assert sb.attendance_description("沒有時間", date(2027, 3, 20), "晚上") is None


def test_vote_choice_updates_excludes_recent_winner_of_same_award():
    form = {"items": [
        {"title": "問題1.請問您已確實閱讀？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "是，我已閱讀完畢"}]}}}},
        {"title": "問題3.您覺得『家事達人獎』，應由誰得獎呢？", "questionItem": {"question": {"choiceQuestion": {
            "options": [{"value": "舊人"}, {"isOther": True}]}}}},
        {"title": "問題4.您覺得『最佳進步獎』，應由誰得獎呢？", "questionItem": {"question": {"choiceQuestion": {
            "options": []}}}},
    ]}
    reqs = sb.vote_choice_updates(form, ["王小明", "檸檬人1", "王小明", "林阿美"], {"家事達人獎": {"王小明"}})
    opts = [r["updateItem"]["item"]["questionItem"]["question"]["choiceQuestion"]["options"] for r in reqs]
    assert opts == [[{"value": "林阿美"}, {"isOther": True}], [{"value": "王小明"}, {"value": "林阿美"}]]


def make_tree():
    prev = [{"id": "f1", "name": "2026春酒參與統計表單", "mimeType": FORM},
            {"id": "s1", "name": "2026春酒參與統計表單-範本", "mimeType": SHEET},
            {"id": "d1", "name": "春酒抽籤.docx", "mimeType": "application/msword"},
            {"id": "p1", "name": "2026春酒照片", "mimeType": FOLDER},
            {"id": "p2", "name": "遊覽車", "mimeType": FOLDER}]
    prev += [{"id": f"v{a}", "name": f"2025年度投票（{a}）", "mimeType": FORM} for a in sb.ROSTER_AREAS]
    return {sb.SPRING_ROOT_FOLDER_ID: [{"id": "prev", "name": "2026春酒", "mimeType": FOLDER}], "prev": prev}


def test_run_spring_area_dry_run(monkeypatch):
    def fake_export(area, accounts):
        if area == "新竹":
            raise RuntimeError("登入失敗")
        return frame(row("王小明", "0912345678", "2020-01-01", "正常"))

    tree = make_tree()
    monkeypatch.setattr(sb, "export_staff_frame", fake_export)
    monkeypatch.setattr("tools.scheduled_monthly.stored_value_settlement.load_accounts", lambda: {})
    monkeypatch.setattr(sb, "get_forms_service", lambda: (_ for _ in ()).throw(AssertionError("不應呼叫")))
    rollover = Rollover(None, None, "x", 2027, dry_run=True, log=lambda m: None)
    rollover.children = lambda folder_id: tree.get(folder_id, [])
    rollover.spring_event = {"date": date(2027, 3, 13), "period": "中午"}

    names = [(r["old_name"], r["new_name"]) for r in rollover.run("all", names=["春酒專區"])]
    assert ("2026春酒參與統計表單", "2027春酒參與統計表單") in names           # 表單
    assert ("2026春酒參與統計表單-範本", "2027春酒參與統計表單") in names      # 回覆試算表取範本
    assert ("2025年度投票（台北）", "2026年度投票（台北）") in names
    assert ("2026春酒照片", "2027春酒照片") in names and ("遊覽車", "遊覽車") in names
    assert ("春酒抽籤.docx", "春酒抽籤") in names                         # Word 轉 Google 文件
    assert ("專員個資-新竹", "找不到來源") in names


def test_fresh_single_area_is_rejected():
    logs = []
    rollover = Rollover(None, None, "x", 2027, dry_run=True, fresh=True, log=logs.append)
    rollover.children = lambda folder_id: (_ for _ in ()).throw(AssertionError("不應動到資料夾"))
    assert sb.run_spring_area(rollover, "台北") == []
    assert any("只能選全區" in m for m in logs)


def test_one_form_failure_does_not_stop_other_regions(monkeypatch):
    tree = make_tree()
    tree[sb.SPRING_ROOT_FOLDER_ID].append({"id": "cur", "name": "2027春酒", "mimeType": FOLDER})
    tree["cur"] = [{"id": f"n{a}", "name": f"2026年度投票（{a}）", "mimeType": FORM} for a in sb.ROSTER_AREAS]
    monkeypatch.setattr(sb, "export_staff_frame",
                        lambda area, acc: frame(row(f"{area}人", "0912", "2020-01-01", "正常")))
    monkeypatch.setattr("tools.scheduled_monthly.stored_value_settlement.load_accounts", lambda: {})
    monkeypatch.setattr(sb, "get_forms_service", lambda: object())
    updated = []

    def fake_update(rollover, forms, form_id, form_name, names, excluded, area=""):
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


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheets:
    """只支援春酒專區用到的 Sheets 呼叫；values 以 {工作表: {(列,欄): 值}} 存放。"""

    def __init__(self, data):
        self.data = data
        self.appended, self.requests = [], []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, fields=None, **kw):
        a1 = kw.get("range")
        if a1 is None:
            return _Call(lambda: {"sheets": [{"properties": {"title": t, "sheetId": i}}
                                             for i, t in enumerate(self.data)]})
        cells = self.data.get(a1.split("!")[0].strip("'"), {})
        if not cells:
            return _Call(lambda: {})
        start = re.match(r"([A-Z]+)(\d+)", a1.split("!")[1]).groups()
        r0, c0 = int(start[1]), col_index(start[0])
        rows = sorted({r for r, _ in cells if r >= r0})
        return _Call(lambda: {"values": [[cells.get((r, c), "") for c in range(c0, max(c for _, c in cells) + 1)]
                                         for r in rows]})

    def batchClear(self, spreadsheetId, body):
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
            if "updateSheetProperties" in r:
                props = r["updateSheetProperties"]["properties"]
                old = list(self.data)[props["sheetId"]]
                self.data = {(props["title"] if k == old else k): v for k, v in self.data.items()}
        return _Call(lambda: {})


def cells(rows, start_row=1, start_col=0):
    return {(start_row + i, start_col + c): v for i, row in enumerate(rows) for c, v in enumerate(row)}


def test_prepare_and_update_roster_with_fake_sheets():
    data = {
        "2026春酒專員名冊": {}, "2026春酒內勤名冊": cells([["地區", "姓名"], ["台北/台中內勤", "張齊恩"]]),
        "得獎人提詞": cells([["『家事達人獎』", "『最佳進步獎』"], ["台北/楊超顯", "台北/余世烽"], ["說明", "說明"]]),
        "春酒參與統計": {},
        "春酒統計表": cells([["王小明", "", "", "", "", "", "", "1", "", "2"]], start_row=2, start_col=1),
        "活動流程": {},
    }
    fake = FakeSheets(data)
    rollover = Rollover(None, fake, "x", 2027, dry_run=False, log=lambda m: None)
    rollover.find_file_by_name = lambda name: None

    sb.prepare_response_file(rollover, "resp", {"date": date(2027, 3, 13)})
    assert ["'2025", "台北", "家事達人獎", "楊超顯"] in fake.appended
    assert "2027春酒專員名冊" in fake.data and "2027春酒內勤名冊" in fake.data
    data_writes = [d for d in fake.requests if isinstance(d, dict) and "range" in d]
    assert any(d["range"] == "'活動流程'!A3" and d["values"][0][0] == "日期：2027年03月13日(六)"
               for d in data_writes)

    manual = sb.manual_snapshot(rollover, "resp")
    sb.update_roster(rollover, "resp", {"台北": [["王小明", "'0912", "2020-01-01", "正常"],
                                               ["張齊恩", "'0911", "2020-01-01", "正常"]]})
    fake.data["2027春酒專員名冊"] = cells([["台北", "王小明"], ["台北", "張齊恩"]], start_row=2)
    sb.repair_formulas(rollover, "resp", manual)
    kept = next(d for d in fake.requests if isinstance(d, dict) and d.get("range") == "'春酒統計表'!I2")
    assert kept["values"] == [["1", "", "2"], ["", "", ""]]   # 王小明的眷屬人數保留；張齊恩列在內勤


def test_response_formula_pulls_linked_tab():
    assert sb.response_formula("參與回應", "A2:G") == \
        "=IFERROR(FILTER('參與回應'!A2:G,'參與回應'!A2:A<>\"\"),\"\")"
    assert sb.RESPONSE_TABS["春酒獎項投票-台北"] == ("投票回應-台北", "A2:H")


def test_region_tag_and_response_tab_target():
    form = {"items": [{"title": "問題2.請問您的姓名是？", "questionItem": {}},
                      {"title": "問題3.『家事達人獎』", "questionItem": {}}]}
    reqs = sb.region_tag_updates(form, "台北")
    assert [r["updateItem"]["item"]["title"] for r in reqs] == ["問題2.請問您的姓名是？（台北）"]
    assert sb.region_tag_updates({"items": [reqs[0]["updateItem"]["item"]]}, "台北") == []
    assert sb.response_tab_target(["時間戳記", "問題2.請問您的姓名是？（新竹）", "『家事達人獎』"]) == "投票回應-新竹"
    assert sb.response_tab_target(["時間戳記", "問題3.請問您的飲食習慣需求是？"]) == "參與回應"
    assert sb.response_tab_target(["時間戳記", "『家事達人獎』"]) is None


def test_link_response_tabs_renames_new_tabs():
    data = {"春酒參與統計": {}, "春酒獎項投票-台北": {},
            "表單回應 1": cells([["時間戳記", "問題1.請問您的中文全名是？", "問題3.請問您的飲食習慣需求是？"]]),
            "表單回應 2": cells([["時間戳記", "問題2.姓名（台北）", "『家事達人獎』"]])}
    fake = FakeSheets(data)
    rollover = Rollover(None, fake, "x", 2027, dry_run=False, log=lambda m: None)
    sb.link_response_tabs(rollover, "resp")
    assert "參與回應" in fake.data and "投票回應-台北" in fake.data
    assert any(isinstance(d, dict) and d.get("range") == "'春酒獎項投票-台北'!A2" for d in fake.requests)


def test_update_vote_form_text_name_question():
    """姓名是簡答題（只加地區標題），不可因沒有 choiceQuestion 而中斷整份表單更新。"""
    form = {"items": [
        {"title": "問題2.請問您的姓名是？", "questionItem": {"question": {"textQuestion": {}}}},
        {"title": "問題3.\n您覺得『家事達人獎』，應由誰得獎呢？",
         "questionItem": {"question": {"choiceQuestion": {"type": "RADIO", "options": [{"value": "舊人"}]}}}},
    ]}
    sent = []

    class Forms:
        def forms(self):
            return self

        def get(self, formId):
            return type("R", (), {"execute": lambda _: form})()

        def batchUpdate(self, formId, body):
            sent.append(body)
            return type("R", (), {"execute": lambda _: {}})()

    rollover = type("Ro", (), {"dry_run": False, "log": lambda self, m: None})()
    sb.update_vote_form(rollover, Forms(), "f", "2026年度投票（台北）", ["王小明"], {}, "台北")
    reqs = sent[0]["requests"]
    assert len(reqs) == 2
    assert reqs[1]["updateItem"]["item"]["title"].endswith("（台北）")
