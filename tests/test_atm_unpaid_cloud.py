"""ATM 待付款同步改由雲端排程執行。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.scheduling import schedule_settings as ss
from tools.scheduled_daily import atm_unpaid

TZ = ZoneInfo("Asia/Taipei")


def _enabled_atm_rule():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "atm_unpaid")
    return ss.parse_row([True] + row[1:])


def test_atm_rule_runs_every_half_hour_on_workdays_only():
    rule = _enabled_atm_rule()
    assert rule.errors == []
    workday = lambda d: d.weekday() < 5 and d != date(2026, 12, 25)
    due = lambda hm, day="2026-11-05": [r.key for r in ss.due([rule], datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=TZ), workday)]
    assert due("09:00") == due("09:30") == due("18:00") == ["atm_unpaid"]
    assert due("08:30") == due("18:30") == due("09:10") == []
    assert due("09:30", "2026-11-07") == []          # 週六
    assert due("09:30", "2026-12-25") == []          # 例假日
    assert ss.dispatch_for(rule, datetime(2026, 11, 5, 9, 30, tzinfo=TZ)) == ("scheduled_atm_unpaid.yml", {})


def test_atm_rule_ships_disabled_until_mac_schedule_removed():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "atm_unpaid")
    assert row[0] is False


def test_run_prints_only_counts(monkeypatch, capsys):
    import types, sys

    def fake_sync(date_until):
        print("王小明 訂單 A123 已貼上")          # 逐筆訊息不能出現在公開紀錄
        return {"台北": {"pasted": 2, "skipped_duplicates": 1, "status_check": {"flagged": 0}},
                "台中": {"pasted": 0, "skipped_duplicates": 3}}

    fake = types.SimpleNamespace(run_scheduled_unpaid_sync=fake_sync)
    monkeypatch.setitem(sys.modules, "tools.memo_system.atm", fake)
    monkeypatch.setattr(atm_unpaid, "_log", lambda *a: None)
    import tools.memo_system as pkg
    monkeypatch.setattr(pkg, "atm", fake, raising=False)
    assert atm_unpaid.run("2026-11-05") == 0
    out = capsys.readouterr().out
    assert "王小明" not in out and "A123" not in out
    assert "台北：新增 2 筆、略過重複 1 筆、已取消標記 0 筆" in out
    assert "台中：新增 0 筆、略過重複 3 筆" in out


def test_run_reports_failure(monkeypatch, capsys):
    import types, sys

    def boom(date_until):
        raise RuntimeError("台北：登入失敗")

    fake = types.SimpleNamespace(run_scheduled_unpaid_sync=boom)
    monkeypatch.setitem(sys.modules, "tools.memo_system.atm", fake)
    import tools.memo_system as pkg
    monkeypatch.setattr(pkg, "atm", fake, raising=False)
    monkeypatch.setattr(atm_unpaid, "_log", lambda *a: None)
    assert atm_unpaid.run("2026-11-05") == 1
    assert "登入失敗" in capsys.readouterr().out


def test_detail_goes_to_private_log_not_stdout(monkeypatch, capsys):
    from tools.scheduled_daily import atm_unpaid
    from tools.memo_system import atm

    def fake_sync(date_until=None, ui_logger=None):
        print("  - 2026.11　LC00219397　黃妙禎　總金額4800 - 車馬費0 = 4800")
        print("⚠️ 略過 LC00219397 黃妙禎：單張複查狀態為「已付款」")
        print("單張複查完成：0／1 筆確認仍待付款")
        return {"台北": {"pasted": 0, "skipped_duplicates": 0}}

    logged = []
    monkeypatch.setattr(atm, "run_scheduled_unpaid_sync", fake_sync)
    monkeypatch.setattr(atm_unpaid, "_log", lambda status, message: logged.append(message))
    assert atm_unpaid.run("2026-11-05") == 0
    out = capsys.readouterr().out
    assert "黃妙禎" not in out and "LC00219397" not in out
    assert "略過 LC00219397 黃妙禎：單張複查狀態為「已付款」" in logged[0]
    assert "單張複查完成：0／1" in logged[0]


def test_scheduled_sync_always_uses_production_backend(monkeypatch):
    from tools.scheduled_daily import atm_unpaid
    from tools.memo_system import atm, memo

    seen = {}
    memo.set_env("dev")
    monkeypatch.setattr(atm, "run_scheduled_unpaid_sync",
                        lambda date_until=None, ui_logger=None: seen.setdefault("url", memo.BASE_URL) and {})
    monkeypatch.setattr(atm_unpaid, "_log", lambda *a: None)
    assert atm_unpaid.run("2026-11-05") == 0
    assert seen["url"] == "https://backend.lemonclean.com.tw"
