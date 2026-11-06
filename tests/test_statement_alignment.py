import unittest

from tools.finance_management import statement_alignment as sa

FUBON = "富邦更新"


def bank_row(date="2026/10/01", time_="2026/10/01 10:00:00", out="", inc="100",
             note="客戶", category="匯款收入", label="2026.10-清潔-服務費用", note_mark=""):
    # A..L：勾選、帳務日、交易時間、摘要、支出、存入、餘額、附註、科目、人工標記、K、L
    return ["TRUE", date, time_, "ＣＤ轉收", out, inc, "1,000", note, category, note_mark,
            date, label]


def manual_row(category="google行銷費", label="2026.07-google行銷費", out="5,532"):
    return ["", "", "", "", out, "", "", "", category, "", "", label]


def mirror_row(update_row, mirror_row_number, invoice=""):
    r = mirror_row_number
    return [
        f'=TEXT(E{r},"yyyy.mm")', f"=LEFT(F{r},7)", f"='富邦更新'!I{update_row}", invoice,
        f"='富邦更新'!K{update_row}", f"='富邦更新'!L{update_row}", f"='富邦更新'!M{update_row}",
        f"='富邦更新'!N{update_row}", f"='富邦更新'!O{update_row}",
    ]


HEADER = [["", "帳務日期", "交易時間"]]
MIRROR_HEADER = [["", "", "科目"]]


class ShiftFormulaTest(unittest.TestCase):
    def test_shifts_same_sheet_and_update_sheet_separately(self):
        self.assertEqual(
            sa.shift_formula("='富邦更新'!I4", FUBON, row_delta=1, update_delta=50), "='富邦更新'!I54")
        self.assertEqual(
            sa.shift_formula('=TEXT(E2,"yyyy.mm")', FUBON, row_delta=3, update_delta=50),
            '=TEXT(E5,"yyyy.mm")')

    def test_keeps_strings_absolute_rows_and_function_names(self):
        formula = '=IF($E$2<>0,LEFT(F2,7)&"E2","")'
        self.assertEqual(sa.shift_formula(formula, FUBON, row_delta=1, update_delta=9),
                         '=IF($E$2<>0,LEFT(F3,7)&"E2","")')

    def test_reads_referenced_row_for_category_column(self):
        self.assertEqual(sa.referenced_update_row("='富邦更新'!I253", FUBON, "I"), 253)
        self.assertIsNone(sa.referenced_update_row("='富邦更新'!K253", FUBON, "I"))
        self.assertIsNone(sa.referenced_update_row("匯款收入", FUBON, "I"))


class RowYearTest(unittest.TestCase):
    def test_prior_year_transaction_posted_new_year_counts_as_prior_year(self):
        # 李維芯：帳務日 2026/01/01，交易時間 2025/12/31 22:21
        self.assertEqual(sa.row_year(bank_row(date="2026/01/01", time_="2025/12/31 22:21:09")), 2025)


class PlanAlignmentTest(unittest.TestCase):
    def test_already_aligned_needs_no_writes(self):
        update = HEADER + [bank_row() for _ in range(5)]          # 更新第 2–6 列
        mirror = MIRROR_HEADER + [mirror_row(r, r) for r in range(2, 9)]  # 尾巴預拉到 8
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦", 2026)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.writes, [])
        self.assertEqual(plan.message, "已對齊")

    def test_hsinchu_jump_to_manual_block_gets_realigned(self):
        # 更新：銀行資料 2–10，11–20 空白，21–23 手動列（沒有日期）
        update = HEADER + [bank_row() for _ in range(9)] + [[""]] * 10 + [manual_row() for _ in range(3)]
        # 對應分頁：2–7 抓 2–7，第 8 列直接跳到 21（漏了 8–10），尾巴到 12
        mirror = MIRROR_HEADER + [mirror_row(r, r) for r in range(2, 8)]
        mirror += [mirror_row(u, m) for m, u in zip(range(8, 13), range(21, 26))]
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦", 2026)
        self.assertTrue(plan.ok, plan.message)
        self.assertEqual(plan.first_divergent_row, 8)
        self.assertEqual(plan.missing_update_rows, [8, 9, 10])
        refs = {m: sa.referenced_update_row(v[2], FUBON, "I") for m, v in plan.writes}
        self.assertEqual(refs, {8: 8, 9: 9, 10: 10, 11: 21, 12: 22, 13: 23})
        row8 = dict(plan.writes)[8]
        self.assertEqual(row8[0], '=TEXT(E8,"yyyy.mm")')
        self.assertEqual(row8[5], "='富邦更新'!L8")

    def test_taipei_skipped_rows_and_prior_year_rows(self):
        update = HEADER + [
            bank_row(date="2026/01/01", time_="2025/12/31 22:21:09"),            # 2 去年
            bank_row(date="2026/01/01", time_="2025/12/31 22:21:09", inc=""),    # 3 去年（拆單）
        ] + [bank_row() for _ in range(9)]                                      # 4–12
        # 對應分頁從更新第 4 列開始抓；第 5 列跳過 7、8；接著一段空白列
        mirror = MIRROR_HEADER + [mirror_row(u, m) for m, u in [(2, 4), (3, 5), (4, 6), (5, 9), (6, 10)]]
        mirror += [["", "", "", "", "", "", "", "", ""]] * 3          # 7–9 空白
        mirror += [mirror_row(u, m) for m, u in [(10, 11), (11, 12)]]
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦公司戶", 2026)
        self.assertTrue(plan.ok, plan.message)
        self.assertEqual(plan.missing_update_rows, [7, 8])
        refs = {m: sa.referenced_update_row(v[2], FUBON, "I") for m, v in plan.writes}
        # 第 2–4 列不動；第 5 列起依序 7–12，原本的尾巴接在後面
        self.assertEqual(refs, {5: 7, 6: 8, 7: 9, 8: 10, 9: 11, 10: 12, 11: 13})
        self.assertNotIn(2, refs)

    def test_grows_when_mirror_is_shorter_than_update(self):
        update = HEADER + [bank_row() for _ in range(6)]   # 2–7
        mirror = MIRROR_HEADER + [mirror_row(r, r) for r in range(2, 5)]  # 只到 4
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦", 2026)
        refs = {m: sa.referenced_update_row(v[2], FUBON, "I") for m, v in plan.writes}
        self.assertEqual(refs, {5: 5, 6: 6, 7: 7})

    def test_manual_values_in_rewrite_range_block_the_sheet(self):
        update = HEADER + [bank_row() for _ in range(6)]
        mirror = MIRROR_HEADER + [mirror_row(2, 2), mirror_row(3, 3), mirror_row(5, 4, invoice="AB12345678")]
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦公司戶", 2026)
        self.assertFalse(plan.ok)
        self.assertEqual(plan.writes, [])
        self.assertIn("D4", plan.message)

    def test_tail_pointing_back_to_counted_rows_is_fixed(self):
        update = HEADER + [bank_row() for _ in range(3)]     # 2–4
        mirror = MIRROR_HEADER + [mirror_row(r, r) for r in range(2, 5)] + [mirror_row(3, 5)]
        plan = sa.plan_alignment(update, mirror, FUBON, "富邦", 2026)
        self.assertEqual(plan.duplicate_update_rows, [3])
        refs = {m: sa.referenced_update_row(v[2], FUBON, "I") for m, v in plan.writes}
        self.assertEqual(refs, {5: 5})


class AuditAndBackfillTest(unittest.TestCase):
    def test_find_unlabeled_skips_marked_continuation_and_prior_year_rows(self):
        update = HEADER + [
            bank_row(category="", label="#N/A"),                     # 2 待確認
            bank_row(category="", label="#N/A", note_mark="不標"),   # 3 註記不標
            bank_row(category="", label="#N/A", inc=""),             # 4 拆單延續列
            bank_row(date="2026/01/01", time_="2025/12/31 22:00:00", category=""),  # 5 去年
            bank_row(category="匯款收入", label="2026.10-"),          # 6 標記不完整
            bank_row(),                                               # 7 已標好
        ]
        rows = [item.row for item in sa.find_unlabeled(update, FUBON, 2026)]
        self.assertEqual(rows, [2, 6])
        # 對應分頁從第 3 列才開始用公式抓 → 第 2 列是在對應分頁手打，不算待確認
        rows = [item.row for item in sa.find_unlabeled(update, FUBON, 2026, from_row=3)]
        self.assertEqual(rows, [6])

    def test_first_referenced_row_skips_hand_typed_rows(self):
        mirror = MIRROR_HEADER + [["2026.01", "2026.01", "辦公室租金費"]] + [mirror_row(70, 3)]
        self.assertEqual(sa.first_referenced_row(mirror, FUBON), 70)

    def test_backfill_targets_bank_rows_without_formulas(self):
        with_formula = bank_row()
        without = bank_row()[:10]
        update = HEADER + [with_formula, without, manual_row()[:10]]
        self.assertEqual(sa.plan_formula_backfill(update, FUBON), [3])

    def test_label_missing_patterns(self):
        for value in ["", "#N/A", "2026.05-", "2026.10 ", "--,,", "-"]:
            self.assertTrue(sa.label_missing(value), value)
        for value in ["2026.10-沖正", "2026.10 內勤薪資"]:
            self.assertFalse(sa.label_missing(value), value)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 端到端：假的 Sheets 服務，確認盤點不寫、執行時依序補公式→套規則→對齊
# ---------------------------------------------------------------------------
import re
import sys
import types
from unittest.mock import patch


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheets:
    def __init__(self, sheets):
        self.sheets = sheets          # title -> list of rows
        self.writes = []              # (range, values)

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, fields=None, **_):
        if fields:
            return _Req(lambda: {"sheets": [{"properties": {"title": t}} for t in self.sheets]})
        title = re.match(r"'(.+)'!", range).group(1)
        return _Req(lambda: {"values": [list(r) for r in self.sheets[title]]})

    def batchUpdate(self, spreadsheetId=None, body=None):
        def run():
            for item in body["data"]:
                m = re.match(r"'(.+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", item["range"])
                title, c1, r1 = m.group(1), m.group(2), int(m.group(3))
                start = sa_col(c1)
                grid = self.sheets[title]
                for dr, vals in enumerate(item["values"]):
                    while len(grid) < r1 + dr:
                        grid.append([])
                    row = grid[r1 + dr - 1]
                    while len(row) < start - 1 + len(vals):
                        row.append("")
                    row[start - 1:start - 1 + len(vals)] = vals
                self.writes.append((item["range"], item["values"]))
            return {}
        return _Req(run)


def sa_col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


class MaintainSheetTest(unittest.TestCase):
    def _run(self, fake, dry_run, rules_calls):
        stubs = {
            "tools.common.config_loader": types.SimpleNamespace(get_sheets_service=lambda: fake),
            "tools.finance_management.statement_registry": types.SimpleNamespace(
                resolve_statement_location=lambda area, report, service=None, year=None: ("sid", report)),
            "tools.finance_management.statement_years": types.SimpleNamespace(taipei_year=lambda: 2026),
            "tools.bank_statement.sheet_filter": types.SimpleNamespace(
                _fubon_formulas=lambda r, atm: [f"=K{r}", f"=L{r}:{atm}", "", "", "", ""],
                _yuanta_formulas=lambda r: [f"=L{r}"] * 6),
        }
        with patch.dict(sys.modules, stubs):
            return sa.maintain_sheet(
                "新竹", FUBON, dry_run=dry_run, service=fake,
                apply_rules_fn=lambda area: rules_calls.append(area) or {"updated_rows": 2, "inserted_rows": 0},
                atm_lookup=lambda area, year: f"ATM-{area}-{year}",
            )

    def _fake(self):
        update = HEADER + [bank_row() for _ in range(3)] + [bank_row()[:10]]  # 第 5 列缺公式
        mirror = MIRROR_HEADER + [mirror_row(r, r) for r in range(2, 5)]     # 只抓到第 4 列
        return FakeSheets({FUBON: update, "富邦": mirror})

    def test_dry_run_reports_without_writing(self):
        fake, calls = self._fake(), []
        lines = self._run(fake, True, calls)
        self.assertEqual(fake.writes, [])
        self.assertEqual(calls, [])
        text = "\n".join(lines)
        self.assertIn("1 列缺公式", text)
        self.assertIn("從第 5 列起重新對齊 1 列", text)

    def test_apply_backfills_runs_rules_then_aligns(self):
        fake, calls = self._fake(), []
        self._run(fake, False, calls)
        self.assertEqual(calls, ["新竹"])
        ranges = [r for r, _ in fake.writes]
        self.assertEqual(ranges[0], "'富邦更新'!K5:P5")
        self.assertEqual(fake.writes[0][1][0][1], "=L5:ATM-新竹-2026")
        self.assertEqual(ranges[1], "'富邦'!A5:I5")
        self.assertEqual(fake.sheets["富邦"][4][2], "='富邦更新'!I5")
