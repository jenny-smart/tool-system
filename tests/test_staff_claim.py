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
        start, end = a1.split(":")
        start_no = int("".join(c for c in start if c.isdigit()))
        end_no = int("".join(c for c in end if c.isdigit()))
        return _Request({"values": rows[start_no - 1:end_no]})

    def batchUpdate(self, spreadsheetId, body):
        self.batch_bodies.append(body)
        return _Request({})


class _Service:
    def __init__(self, sheets):
        self.values_api = _Values(sheets)

    def spreadsheets(self):
        return self

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
    def _run(self, claim_rows, start_row=2, end_row=None):
        sheets = {"專員請款": [CLAIM_HEADER] + claim_rows, "專員個人資料": STAFF_ROWS}
        service = _Service(sheets)
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

    def test_period_is_required(self):
        with self.assertRaises(ValueError):
            staff_claim.run_claim_batch("台北", 2, 3, "", service=_Service({}))


if __name__ == "__main__":
    unittest.main()
