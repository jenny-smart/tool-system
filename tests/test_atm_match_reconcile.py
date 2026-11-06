"""ATM 對帳 ②＋③ 每日 08:10 排程，以及待配對列 G／I／N／O 規則。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tools.memo_system import atm
from tools.scheduling import schedule_settings as ss

TZ = ZoneInfo("Asia/Taipei")


# ── 排程 ─────────────────────────────────────────────
def test_rule_runs_daily_at_0810_including_weekends():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "atm_match_reconcile")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 6, 8, 10, tzinfo=TZ)) == ["atm_match_reconcile"]
    assert due(datetime(2026, 11, 7, 8, 10, tzinfo=TZ)) == ["atm_match_reconcile"]  # 週六照跑
    assert due(datetime(2026, 11, 6, 8, 0, tzinfo=TZ)) == []
    assert ss.dispatch_for(rule, datetime(2026, 11, 6, 8, 10, tzinfo=TZ)) == (
        "scheduled_atm_unpaid.yml", {"target": "match_reconcile"})


# ── N/O 預設 ─────────────────────────────────────────
@pytest.mark.parametrize("amount,text,expected", [
    (2000, "", ("清潔", "新訓費")),
    ("2,000", "", ("清潔", "新訓費")),
    (4800, "", ("清潔", "服務費用")),
    (1200, "異動服務收款 11/05加時1小時", ("清潔", "加時")),
    (2000, "異動服務收款 工具組", ("清潔", "工具組")),
])
def test_default_service_fee(amount, text, expected):
    assert atm.default_service_fee(amount, text) == expected


# ── ③ 只挑確定配對的列 ───────────────────────────────
def _row(**cols):
    row = [""] * 21
    for letter, value in cols.items():
        row[ord(letter) - 65] = value
    return row


def test_rows_ready_for_reconcile_only_confirmed_matches():
    rows = [
        _row(),  # 標題
        _row(E="4800", J="LC00219084", T="已配對"),
        _row(E="4800", J="LC00219085", T="需確認"),
        _row(E="2000", J="", T="已配對"),                       # 非訂單（新訓費）
        _row(E="4800", J="LC00219086", T="已更新系統"),
        _row(E="", J="LC00219087", T="已配對"),                  # 下方待配對區
        _row(E="4800", J="LC00219088", M="取消訂單", T="已配對"),
        _row(E="9600", J="LC00219089", T="已配對（多訂單加總）"),
    ]
    assert atm.rows_ready_for_reconcile(rows) == [2, 8]


# ── G 公式／下拉選單 ─────────────────────────────────
class FakeSpreadsheet:
    def __init__(self, validation_rows):
        self.validation_rows = validation_rows  # {col: {row,...}}
        self.requests = []

    def fetch_sheet_metadata(self, params=None):
        rows = []
        for r in range(1, 20):
            rows.append({"values": [
                {"dataValidation": {"condition": {}}} if r in self.validation_rows.get(c, set()) else {}
                for c in range(9, 16)
            ]})
        return {"sheets": [{"data": [{"startRow": 0, "startColumn": 8, "rowData": rows}]}]}

    def batch_update(self, body):
        self.requests.extend(body["requests"])


class FakeWorksheet:
    id = 42
    title = "ATM"

    def __init__(self, g_formulas, validation_rows):
        self.g = g_formulas
        self.spreadsheet = FakeSpreadsheet(validation_rows)
        self.updates = []

    def get(self, a1, value_render_option=None):
        return [[v] if v else [] for v in self.g]

    def update(self, a1, values, value_input_option=None):
        self.updates.append((a1, values))


def test_ensure_format_copies_g_formula_and_dropdowns_from_above():
    formula = '=I2&"-"&N2'
    ws = FakeWorksheet([""] + [formula] * 9 + ["", ""], {9: {2, 3}, 14: {2}, 15: {2}})
    result = atm.ensure_candidate_row_format(ws, [11, 12])
    assert result == {"g_filled": 2, "dropdown_copied": 6}
    g_copies = [r["copyPaste"] for r in ws.spreadsheet.requests if r["copyPaste"]["pasteType"] == "PASTE_FORMULA"]
    assert [(c["source"]["startRowIndex"], c["destination"]["startRowIndex"]) for c in g_copies] == [(9, 10), (9, 11)]
    i_copies = [r["copyPaste"] for r in ws.spreadsheet.requests
                if r["copyPaste"]["pasteType"] == "PASTE_DATA_VALIDATION"
                and r["copyPaste"]["destination"]["startColumnIndex"] == 8]
    assert {c["source"]["startRowIndex"] for c in i_copies} == {2}  # 第 3 列（最近的上方下拉）
    assert ws.updates == []


def test_ensure_format_keeps_existing_g_and_falls_back_when_no_formula_above():
    ws = FakeWorksheet(["", "", "已有值"], {})
    result = atm.ensure_candidate_row_format(ws, [2, 3])
    assert result["g_filled"] == 1
    assert ws.updates == [("G2", [['=I2&"-"&N2&"-"&O2&","&J2&","&K2']])]


# ── 雲端入口 ─────────────────────────────────────────
def test_run_prints_counts_only(monkeypatch, capsys):
    from tools.scheduled_daily import atm_match_reconcile as job
    from tools.memo_system import memo

    def fake():
        print("✅ 第5列：末碼+金額 → LC00219084 許文菁 $4800")
        return {"台北": {"match": {"success": 3}, "reconcile": {"success": 2, "failed": 0},
                         "reconcile_rows": [5, 6]},
                "台中": {"match": {}, "reconcile": {}, "reconcile_rows": []}}

    logged = []
    monkeypatch.setattr(atm, "run_scheduled_match_and_reconcile", fake)
    monkeypatch.setattr(job, "_log", lambda status, message: logged.append((status, message)))
    memo.set_env("dev")
    assert job.run() == 0
    out = capsys.readouterr().out
    assert "許文菁" not in out and "LC00219084" not in out
    assert "台北：② 配對成功 3" in out and "③ 更新系統 2／2 列" in out
    assert "許文菁" in logged[0][1]
    assert memo.BASE_URL == "https://backend.lemonclean.com.tw"


def test_run_reports_partial_results_on_failure(monkeypatch, capsys):
    from tools.scheduled_daily import atm_match_reconcile as job

    def boom():
        exc = RuntimeError("台中：登入失敗")
        exc.results = {"台北": {"match": {"success": 1}, "reconcile": {}, "reconcile_rows": []}}
        raise exc

    monkeypatch.setattr(atm, "run_scheduled_match_and_reconcile", boom)
    monkeypatch.setattr(job, "_log", lambda *a: None)
    assert job.run() == 1
    out = capsys.readouterr().out
    assert "台中：登入失敗" in out and "台北：② 配對成功 1" in out
