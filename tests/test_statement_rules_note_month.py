"""L欄年月來源＝H（附註年月）、規則衝突、只補未標記——用台北/桃園/新竹實際出現過的列。"""
import unittest
from unittest.mock import patch

from tools.finance_management import statement_rules
from tools.finance_management.statement_alignment import find_month_mismatches
from tools.finance_management.statement_rules import Rule, _month_from_note


def _row(d="勞保費", h="11月013870040T", k="2026/01/02", out=45946, i="", l=""):
    row = [""] * 18
    row[1] = k
    row[2] = f"{k} 04:47:13"
    row[3] = d
    row[4] = out
    row[7] = h
    row[8] = i
    row[10] = k
    row[11] = l
    return row


def _rule(name, d, set_i, suffix, source="H", offset=0, action="更新原列"):
    return Rule([True, name, "D", "等於", d, "", "", "", set_i, offset, suffix, False,
                 action, "", "", source, ""])


class NoteMonthTest(unittest.TestCase):
    def test_month_only_note_takes_latest_month_not_after_posting_date(self):
        self.assertEqual(_month_from_note(_row(h="11月013870040T", k="2026/01/02")), (2025, 11))
        self.assertEqual(_month_from_note(_row(h="07月013870040T", k="2026/09/02")), (2026, 7))
        self.assertEqual(_month_from_note(_row(h="01月142780738", k="2026/01/16")), (2026, 1))

    def test_explicit_year_formats(self):
        self.assertEqual(_month_from_note(_row(h="202609台北營業稅")), (2026, 9))
        self.assertEqual(_month_from_note(_row(h="2025.12 補繳")), (2025, 12))
        self.assertEqual(_month_from_note(_row(h="115年10月 營業稅")), (2026, 10))

    def test_account_numbers_are_not_mistaken_for_months(self):
        self.assertIsNone(_month_from_note(_row(h="0082500017480482")))
        self.assertIsNone(_month_from_note(_row(h="7102230970401300")))

    def test_rule_uses_note_month_and_skips_when_note_has_none(self):
        rule = _rule("內勤勞保費", "勞保費", "內勤勞保費", "內勤勞保費")
        # 台北 2026/09 扣款、附註 07月 → 以前標成 2026.09，現在是 2026.07
        self.assertEqual(rule.l_value(_row(h="07月013870040T", k="2026/09/02")), "2026.07-內勤勞保費")
        # 桃園有一筆附註空白 → 不猜
        self.assertIsNone(rule.l_value(_row(h="", k="2026/07/16")))


class ConflictAndUnlabeledTest(unittest.TestCase):
    def _run(self, rows, rules, **kwargs):
        updates = []

        class _Values:
            def batchUpdate(self, spreadsheetId, body):
                updates.extend(body["data"])
                return self

            def execute(self):
                return {}

        class _Service:
            def spreadsheets(self):
                return self

            def values(self):
                return _Values()

        with patch.object(statement_rules, "load_rules", return_value=rules), \
                patch.object(statement_rules, "resolve_statement_location", return_value=("sid", "富邦更新")), \
                patch.object(statement_rules, "_read_values", return_value=[["標題"], *rows]), \
                patch.object(statement_rules, "get_sheets_service", return_value=_Service()):
            result = statement_rules._apply_rules_impl("台北", **kwargs)
        return result, {u["range"]: u["values"][0][0] for u in updates}

    def test_two_rules_with_different_results_skip_the_row(self):
        rules = [
            _rule("內勤退休金", "勞退", "內勤退休金", "內勤退休金"),
            _rule("內勤退休金", "勞退", "內勤薪資", "內勤薪資"),
        ]
        result, written = self._run([_row(d="勞退", h="07月013870040T", k="2026/10/02", out=34628)], rules)
        self.assertEqual(result["conflict_rows"], 1)
        self.assertIn("第2列", result["diagnostics"])
        self.assertEqual(written, {})

    def test_only_unlabeled_leaves_labeled_rows_alone(self):
        rules = [_rule("內勤勞保費", "勞保費", "內勤勞保費", "內勤勞保費")]
        labeled = _row(h="07月013870040T", k="2026/09/02", i="內勤勞保費", l="2026.09-內勤勞保費")
        blank = _row(h="08月013870040T", k="2026/10/02", l="2026.10-")
        result, written = self._run([labeled, blank], rules, only_unlabeled=True)
        self.assertNotIn("'富邦更新'!L2", written)
        self.assertEqual(written["'富邦更新'!I3"], "內勤勞保費")
        self.assertEqual(written["'富邦更新'!L3"], "2026.08-內勤勞保費")


class MonthMismatchAuditTest(unittest.TestCase):
    def test_reports_labeled_rows_whose_month_differs_from_note(self):
        rules = [_rule("內勤健保費", "健保費", "內勤健保費", "內勤健保費")]
        values = [["標題"],
                  _row(d="健保費", h="07月142780738", k="2026/09/16", i="內勤健保費", l="2026.09-內勤健保費"),
                  _row(d="健保費", h="08月142780738", k="2026/10/16", i="內勤健保費", l="2026.08-內勤健保費")]
        found = find_month_mismatches(values, rules, 2026)
        self.assertEqual(len(found), 1)
        self.assertIn("第 2 列", found[0])
        self.assertIn("應為 2026.07-內勤健保費", found[0])


if __name__ == "__main__":
    unittest.main()
