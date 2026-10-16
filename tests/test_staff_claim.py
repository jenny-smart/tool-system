import sys
import types
import unittest


registry_stub = types.ModuleType("tools.bank_statement.internal_payment_registry")
registry_stub.CLEANING_CHANGE_TYPE = "清潔異動設定"
registry_stub.REGISTRY_SHEET_NAME = "財務帳務設定"
registry_stub.find_registry_row = lambda report_type, area: {"試算表ID": f"sheet-{area}"}

config_stub = types.ModuleType("tools.common.config_loader")
config_stub.get_sheets_service = lambda: None

sys.modules.setdefault("tools.bank_statement.internal_payment_registry", registry_stub)
sys.modules.setdefault("tools.common.config_loader", config_stub)

from tools.field_management import staff_claim  # noqa: E402


CLAIM_HEADER = [
    "提交ID", "提交時間", "專員姓名", "服務日期", "顧客姓名", "顧客電話", "請款項目", "請款子項目",
    "跨區開始區域", "跨區結束區域", "交通方式", "距離站數", "捷運起迄站", "跨區類型", "第二位顧客姓名",
    "第二位顧客電話", "取消客戶類型", "客戶取消時間", "取消時數", "補做時數", "請假專員姓名", "延長原因",
    "延長時數", "主管取消客戶類型", "主管取消時間", "主管取消時數", "其他項目說明", "其他備註", "專員地址",
    "顧客地址1", "顧客地址2", "實際跨區1", "實際跨區2", "檢查跨區1", "檢查跨區2", "給付期別",
    "專員姓名_重複", "請款項目_顯示", "規範補貼", "請款備註", "審核補貼", "審核人", "審核備註",
]
COL = {name: i for i, name in enumerate(CLAIM_HEADER)}

STAFF_ROWS = [
    ["專員姓名", "身份證字號", "地址", "", "", "", "服務日期", "服務時間", "服務時數", "", "", "客戶姓名", "客戶地址", "聯絡電話"],
    ["", "", "", "", "", "", "2026/1/2", "09:00-11:00", "2", "", "", "廖士捷", "台北市南港區園區街48號5樓之2", "937064638"],
    ["", "", "", "", "", "", "2026/1/2", "13:00-16:00", "3", "", "", "王小華", "台北市信義區松仁路100號", "0911222333"],
]


def claim_row(**values):
    row = [""] * len(CLAIM_HEADER)
    for name, value in values.items():
        row[COL[name]] = value
    return row


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


def _parse_a1(a1):
    """'AG2:AR10' → (起欄 index, 起列, 迄欄 index, 迄列或 None)。"""
    def one(part):
        letters = "".join(c for c in part if c.isalpha())
        digits = "".join(c for c in part if c.isdigit())
        col = 0
        for ch in letters:
            col = col * 26 + ord(ch) - 64
        return col - 1, int(digits) if digits else None
    a, b = a1.split(":")
    (c1, r1), (c2, r2) = one(a), one(b)
    return c1, r1, c2, r2


class _Values:
    def __init__(self, sheets):
        self.sheets = sheets
        self.batch_bodies = []

    def get(self, spreadsheetId, range, valueRenderOption=None):
        title, a1 = range.split("!")
        rows = self.sheets[title.strip("'")]
        if a1 == "1:1":
            return _Request({"values": rows[:1]})
        if a1 == "A:Z":
            return _Request({"values": rows})
        c1, r1, c2, r2 = _parse_a1(a1)
        picked = rows[r1 - 1:r2] if r2 else rows[r1 - 1:]
        return _Request({"values": [row[c1:c2 + 1] for row in picked]})

    def batchUpdate(self, spreadsheetId, body):
        self.batch_bodies.append(body)
        # 寫回假資料，讓後續讀取看得到新值
        for item in body["data"]:
            title, a1 = item["range"].split("!")
            c, r, _, _ = _parse_a1(a1 + ":" + a1)
            rows = self.sheets[title.strip("'")]
            while len(rows[r - 1]) <= c:
                rows[r - 1].append("")
            rows[r - 1][c] = item["values"][0][0]
        return _Request({})


class _Service:
    def __init__(self, sheets, notes=None, formulas=None):
        self.values_api = _Values(sheets)
        self.sheets = sheets
        self.notes = notes or {}        # {(row, col): note}，模擬既有備註
        self.formulas = formulas or {}  # {(row, col): formula}，模擬既有公式
        self.format_requests = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId, body):
        self.format_requests.extend(body["requests"])
        return _Request({})

    def get(self, spreadsheetId, fields=None, ranges=None):
        if ranges:
            c1, r1, c2, r2 = _parse_a1(ranges[0].split("!")[1])
            rows = self.sheets["專員請款"]
            row_data = []
            for r in range(r1, r2 + 1):
                values = []
                for c in range(c1, c2 + 1):
                    cell = {"note": self.notes.get((r, c), "")}
                    if (r, c) in self.formulas:
                        cell["userEnteredValue"] = {"formulaValue": self.formulas[(r, c)]}
                    elif r - 1 < len(rows) and c < len(rows[r - 1]) and rows[r - 1][c]:
                        cell["userEnteredValue"] = {"stringValue": rows[r - 1][c]}
                    values.append(cell)
                row_data.append({"values": values})
            return _Request({"sheets": [{"data": [{"rowData": row_data}]}]})
        return _Request({"sheets": [
            {"properties": {"sheetId": 0, "title": "ATM"}},
            {"properties": {"sheetId": staff_claim.CLAIM_SHEET_GIDS["台北"], "title": "專員請款"}},
        ]})

    def values(self):
        return self.values_api


class ClaimRemarkTests(unittest.TestCase):
    def test_site_to_site_travel_claim_includes_both_customers(self):
        remark = staff_claim.build_claim_remark("2026/05/12", "王小明", "跨區車馬費", "", "案場到案場", "陳小華")
        self.assertEqual(remark, "05/12王小明-陳小華跨區車馬費-")

    def test_taichung_travel_claim_name(self):
        remark = staff_claim.build_claim_remark("2026/5/2", "王小明", "車馬費", "", "案場到案場", "陳小華")
        self.assertEqual(remark, "05/02王小明-陳小華車馬費-")

    def test_other_trip_types_keep_single_customer(self):
        remark = staff_claim.build_claim_remark("2026/05/12", "王小明", "跨區車馬費", "", "家到案場", "")
        self.assertEqual(remark, "05/12王小明跨區車馬費-")

    def test_supervisor_uses_sub_item(self):
        remark = staff_claim.build_claim_remark("2026/05/12", "王小明", "主管專用", "病假代班費")
        self.assertEqual(remark, "05/12王小明病假代班費-")


class AddressBookTests(unittest.TestCase):
    def setUp(self):
        self.book = staff_claim.AddressBook(STAFF_ROWS)

    def test_phone_leading_zero_and_date_format_match(self):
        self.assertEqual(
            self.book.lookup("2026/01/02", "廖士捷", "0937064638"),
            "台北市南港區園區街48號5樓之2",
        )

    def test_no_date_only_fallback(self):
        self.assertEqual(self.book.lookup("2026/01/02", "不存在", "0900000000"), "")


class MapTests(unittest.TestCase):
    def test_area_pair_and_link_for_site_to_site(self):
        pair = staff_claim.build_area_pair("案場到案場", "家", "台北市南港區園區街", "台北市信義區松仁路")
        self.assertEqual(pair, "台北市南港區-台北市信義區")
        link = staff_claim.build_map_link("案場到案場", "家", "A地址", "B地址")
        self.assertTrue(link.startswith("https://www.google.com/maps/dir/A"))

    def test_missing_address(self):
        self.assertEqual(staff_claim.build_map_link("家到案場", "", "A", ""), "❌ 地址不完整")


class RunClaimBatchTests(unittest.TestCase):
    def _run(self, claim_rows, start_row=2, end_row=None, notes=None, formulas=None):
        sheets = {"專員請款": [CLAIM_HEADER] + claim_rows, "專員個人資料": STAFF_ROWS}
        service = _Service(sheets, notes, formulas)
        self.service = service
        end_row = end_row or start_row + len(claim_rows) - 1
        result = staff_claim.run_claim_batch("台北", start_row, end_row, "202601-1", service=service)
        written = {}
        for body in service.values_api.batch_bodies:
            for item in body["data"]:
                written[item["range"].split("!")[1]] = item["values"][0][0]
        return result, written

    def test_site_to_site_row_updates_addresses_period_and_remark(self):
        row = claim_row(
            服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="跨區車馬費",
            跨區類型="案場到案場", 第二位顧客姓名="王小華", 第二位顧客電話="911222333",
            請款備註="01/02廖士捷跨區車馬費-",
        )
        result, written = self._run([row])
        self.assertEqual(written["AD2"], "台北市南港區園區街48號5樓之2")
        self.assertEqual(written["AE2"], "台北市信義區松仁路100號")
        self.assertEqual(written["AF2"], "台北市南港區-台北市信義區")
        self.assertTrue(written["AG2"].startswith("https://www.google.com/maps/dir/"))
        self.assertEqual(written["AJ2"], "202601-1")
        self.assertEqual(written["AN2"], "01/02廖士捷-王小華跨區車馬費-")
        self.assertEqual(result["missing_addr1"], [])

    def test_missing_address_keeps_existing_value_and_reports_row(self):
        row = claim_row(
            服務日期="2026/01/03", 顧客姓名="查無", 顧客電話="900000000", 請款項目="工具組運費",
            顧客地址1="手動輸入地址", 請款備註="01/03查無工具組運費-",
        )
        result, written = self._run([row])
        self.assertNotIn("AD2", written)
        self.assertNotIn("AN2", written)
        self.assertNotIn("AF2", written)
        self.assertEqual(written["AJ2"], "202601-1")
        self.assertEqual(result["missing_addr1"], [2])

    def test_blank_rows_in_range_are_untouched(self):
        row = claim_row(服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="工具組運費")
        _, written = self._run([row], end_row=4)
        self.assertIn("AJ2", written)
        self.assertNotIn("AJ3", written)
        self.assertNotIn("AJ4", written)

    def test_unknown_gid_raises(self):
        service = _Service({"專員請款": [CLAIM_HEADER], "專員個人資料": STAFF_ROWS})
        with self.assertRaises(RuntimeError):
            staff_claim.run_claim_batch("台北", 2, 3, "202601-1", service=service, claim_gid=123)

    def _marks(self):
        marks = {}
        for req in self.service.format_requests:
            if "repeatCell" not in req:
                continue
            cell = req["repeatCell"]
            rng = cell["range"]
            marks[(rng["startRowIndex"] + 1, rng["startColumnIndex"])] = cell["cell"]
        return marks

    def test_short_address_is_coloured_with_reason(self):
        row = claim_row(
            服務日期="2026/01/09", 顧客姓名="查無", 顧客電話="900000000", 請款項目="跨區車馬費",
            跨區類型="案場到家", 專員地址="新北市樹林區保安街", 顧客地址1="台北中正區羅斯福路四段68號",
        )
        self._run([row])
        mark = self._marks()[(2, COL["顧客地址1"])]
        self.assertIn("地址少字", mark["note"])
        self.assertIn("台北中正區", mark["note"])
        self.assertEqual(mark["userEnteredFormat"]["backgroundColor"], staff_claim._hex_to_rgb("#F4CCCC"))

    def test_fallback_text_in_address_counts_as_short(self):
        self.assertFalse(staff_claim.address_is_complete("2026/05/04 陳宜欣 918190960"))
        self.assertTrue(staff_claim.address_is_complete("臺中市西屯區台灣大道三段99號"))

    def test_missing_address_and_map_problem_use_different_colours(self):
        row = claim_row(
            服務日期="2026/01/09", 顧客姓名="查無", 顧客電話="900000000", 請款項目="跨區車馬費",
            跨區類型="家到案場", 專員地址="新北市樹林區保安街",
        )
        self._run([row])
        marks = self._marks()
        self.assertIn("查無地址", marks[(2, COL["顧客地址1"])]["note"])
        self.assertIn("地址不完整", marks[(2, COL["實際跨區2"])]["note"])
        self.assertNotEqual(
            marks[(2, COL["顧客地址1"])]["userEnteredFormat"]["backgroundColor"],
            marks[(2, COL["實際跨區2"])]["userEnteredFormat"]["backgroundColor"],
        )

    def test_same_second_customer_is_flagged(self):
        row = claim_row(
            服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="跨區車馬費",
            跨區類型="案場到案場", 第二位顧客姓名="廖士捷", 第二位顧客電話="937064638",
        )
        self._run([row])
        self.assertIn("第二位顧客與顧客相同", self._marks()[(2, COL["請款備註"])]["note"])

    def test_resolved_mark_is_cleared_but_manual_note_kept(self):
        row = claim_row(服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="工具組運費")
        notes = {(2, COL["顧客地址1"]): "⚠️ 地址少字", (2, COL["顧客地址2"]): "人工備註"}
        self._run([row], notes=notes)
        marks = self._marks()
        self.assertEqual(marks[(2, COL["顧客地址1"])]["note"], "")
        self.assertNotIn((2, COL["顧客地址2"]), marks)

    def test_batch_logs_column_check(self):
        row = claim_row(服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="工具組運費")
        logs = []
        service = _Service({"專員請款": [CLAIM_HEADER, row], "專員個人資料": STAFF_ROWS})
        staff_claim.run_claim_batch("台北", 2, 2, "202601-1", service=service, on_progress=logs.append)
        joined = "\n".join(logs)
        self.assertIn("M 客戶地址", joined)
        self.assertIn("AN 請款備註", joined)
        self.assertIn("有效 2 筆", joined)

    def test_missing_staff_column_stops_before_writing(self):
        staff = [[h if h != "客戶地址" else "地址" for h in STAFF_ROWS[0]]] + STAFF_ROWS[1:]
        service = _Service({"專員請款": [CLAIM_HEADER, claim_row(服務日期="2026/01/02", 顧客姓名="A")], "專員個人資料": staff})
        with self.assertRaises(RuntimeError):
            staff_claim.run_claim_batch("台北", 2, 2, "202601-1", service=service)
        self.assertEqual(service.values_api.batch_bodies, [])

    def test_formulas_filled_down_and_duplicates_marked(self):
        base = dict(服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="工具組運費",
                    專員姓名_重複="王小美", 請款備註="01/02廖士捷工具組運費-")
        rows = [claim_row(**base), claim_row(**base), claim_row(**dict(base, 專員姓名_重複="別人"))]
        aq, ar = COL["審核備註"], COL["審核備註"] + 1
        formulas = {(2, aq): "=AN2&AO2", (2, ar): "=countifs(AQ:AQ,AQ2,AK:AK,AK2)"}
        result, _ = self._run(rows, start_row=3, end_row=4, formulas=formulas)
        pastes = [r["copyPaste"] for r in self.service.format_requests if "copyPaste" in r]
        filled = {(p["destination"]["startRowIndex"] + 1, p["destination"]["startColumnIndex"]) for p in pastes}
        self.assertEqual(filled, {(3, aq), (3, ar), (4, aq), (4, ar)})
        self.assertTrue(all(p["source"]["startRowIndex"] == 1 for p in pastes))
        self.assertEqual(result["duplicates"], {3: [2]})
        mark = self._marks()[(3, ar)]
        self.assertIn("重複請款", mark["note"])
        self.assertIn("第 2 列", mark["note"])
        self.assertNotIn((4, ar), self._marks())

    def test_map_link_becomes_clickable(self):
        row = claim_row(
            服務日期="2026/01/02", 顧客姓名="廖士捷", 顧客電話="937064638", 請款項目="跨區車馬費",
            跨區類型="家到案場", 專員地址="新北市五股區新五路二段303號7樓",
        )
        self._run([row])
        links = [r["repeatCell"] for r in self.service.format_requests
                 if "repeatCell" in r and "link" in r["repeatCell"]["fields"]]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["range"]["startColumnIndex"], COL["實際跨區2"])
        uri = links[0]["cell"]["userEnteredFormat"]["textFormat"]["link"]["uri"]
        self.assertTrue(uri.startswith("https://www.google.com/maps/dir/"))

    def test_period_is_required(self):
        with self.assertRaises(ValueError):
            staff_claim.run_claim_batch("台北", 2, 3, "", service=_Service({}))


if __name__ == "__main__":
    unittest.main()
