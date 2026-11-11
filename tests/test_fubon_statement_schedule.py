import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from tools import local_agent_queue as queue
from tools.scheduled_daily import fubon_statement_queue as fsq

TZ = ZoneInfo("Asia/Taipei")


def test_morning_always_other_slots_only_workdays(monkeypatch):
    monkeypatch.setattr("tools.common.tw_holidays.is_workday", lambda day, **kw: day.weekday() < 5)
    monkeypatch.setattr(fsq, "morning_cutoff_hour", lambda: 11)  # 排程設定 10:00,11:00,16:30
    assert fsq.should_queue(datetime(2026, 10, 24, 10, 0, tzinfo=TZ))       # 週六 10:00（Mac 開機才跑）
    assert fsq.should_queue(datetime(2026, 10, 24, 8, 0, tzinfo=TZ))        # 週六 08:00
    assert not fsq.should_queue(datetime(2026, 10, 24, 11, 0, tzinfo=TZ))   # 週六 11:00
    assert not fsq.should_queue(datetime(2026, 10, 25, 16, 30, tzinfo=TZ))  # 週日 16:30
    assert fsq.should_queue(datetime(2026, 10, 23, 16, 30, tzinfo=TZ))      # 週五 16:30


def test_national_holiday_skips_afternoon(monkeypatch):
    monkeypatch.setattr("tools.common.tw_holidays.is_workday", lambda day, **kw: day != date(2026, 10, 9))
    monkeypatch.setattr(fsq, "morning_cutoff_hour", lambda: 11)
    assert not fsq.should_queue(datetime(2026, 10, 9, 11, 0, tzinfo=TZ))


def _task(row, action, area, day, task_id):
    return {"_row": str(row), "task_id": task_id, "action": action, "status": "pending",
            "params_json": json.dumps({"area": area, "start_date": day, "end_date": day})}


def test_only_newest_same_day_download_runs(monkeypatch):
    updates = []
    monkeypatch.setattr(queue, "update_task", lambda row, fields, **kw: updates.append((row, fields["status"])))
    pending = [
        _task(2, "fubon.download", "台北", "2026-10-19", "a"),
        _task(3, "fubon.download", "台中", "2026-10-19", "b"),
        _task(4, "fubon.download", "台北", "2026-10-18", "c"),
        _task(5, "cetustek.login", "台北", "2026-10-19", "d"),
        _task(6, "fubon.download", "台北", "2026-10-19", "e"),
    ]
    kept = queue._skip_superseded_downloads(pending, service=None, spreadsheet_id="")
    assert [t["task_id"] for t in kept] == ["b", "c", "d", "e"]
    assert updates == [(2, "cancelled")]


def test_fubon_start_date_comes_from_report_sheet(monkeypatch):
    created = []
    monkeypatch.setattr(fsq, "should_queue", lambda now: True)
    monkeypatch.setattr(fsq, "create_task", lambda action, params, **kw: created.append(params) or {"task_id": "t"})
    fsq.main()
    assert [p["area"] for p in created] == ["台北", "台中"]
    assert all(p["start_from"] == "sheet" and "start_date" not in p for p in created)

    from tools.local_agent import build_fubon_download, build_yuanta_download

    cmd = build_fubon_download(created[0])
    assert "--start-from-sheet" in cmd and "--start" not in cmd
    assert "--start" in build_fubon_download({"area": "台北", "start_date": "2026-10-01", "end_date": "2026-10-24"})
    assert "--start-from-sheet" in build_yuanta_download({"area": "台北", "start_from": "sheet", "end_date": "2026-10-25"})


def test_last_report_date_reads_bottom_row():
    from datetime import date as d

    from tools.bank_statement.sheet_filter import last_report_date

    assert last_report_date([["2026/10/20 09:00:00"], ["2026/10/22 15:30:01"], [], [""]]) == d(2026, 10, 22)
    assert last_report_date([["交易時間"], []]) is None


def test_yuanta_runs_on_25th_or_previous_workday(monkeypatch):
    from tools.scheduled_daily import yuanta_statement_queue as ysq

    holidays = {date(2026, 10, 10)}
    monkeypatch.setattr("tools.common.tw_holidays.is_workday",
                        lambda day, **kw: day.weekday() < 5 and day not in holidays)
    assert ysq.run_day(2026, 11) == date(2026, 11, 25)   # 週三
    assert ysq.run_day(2026, 10) == date(2026, 10, 23)     # 25 日週日 → 週五
    assert ysq.run_day(2026, 7) == date(2026, 7, 24)       # 25 日週六 → 週五

    created = []
    monkeypatch.setattr(ysq, "create_task", lambda action, params, **kw: created.append((action, params)) or {"task_id": "t"})
    for day, expected in ((date(2026, 10, 25), 0), (date(2026, 10, 23), 2)):
        monkeypatch.setattr(ysq, "datetime", type("D", (datetime,), {"now": classmethod(
            lambda cls, tz=None, day=day: datetime(day.year, day.month, day.day, 11, 0, tzinfo=TZ))}))
        created.clear()
        ysq.main()
        assert len(created) == expected
    assert created[0] == ("yuanta.download", {"start_from": "sheet", "end_date": "2026-10-23", "area": "台北",
                                              "cdp_url": "http://127.0.0.1:9222"})


def test_captcha_written_back_before_task_finishes(monkeypatch):
    import sys

    from tools import local_agent

    script = ("import json,time;print('CAPTCHA_RESULT:'+json.dumps({'image_base64':'aW1n','session_token':'t'}),"
              "flush=True);time.sleep(0.3)")
    updates = []
    monkeypatch.setattr(local_agent, "command_for", lambda task: [sys.executable, "-c", script])
    monkeypatch.setattr(local_agent, "update_task", lambda row, fields, **kw: updates.append(dict(fields)))
    monkeypatch.setattr(local_agent, "append_task_log", lambda *a, **kw: 1)
    monkeypatch.setattr(local_agent, "get_sheets_service", lambda: None)
    monkeypatch.setattr(local_agent, "read_task_status", lambda *a, **kw: "running")
    monkeypatch.setattr(local_agent, "_unified_task_log", lambda *a, **kw: None)
    task = {"_row": "2", "task_id": "x", "action": "fubon.captcha", "params_json": "{}"}
    assert local_agent.run_task(task, service=None, spreadsheet_id="") == 0
    first = updates[0]
    assert "status" not in first and json.loads(first["result_json"])["image_base64"] == "aW1n"
    assert updates[-1]["status"] == "completed"


def test_transfer_download_start_shared_with_report(monkeypatch):
    from datetime import date as d

    from tools.bank_statement import fubon_agent

    account = type("A", (), {"area": "台北"})()
    monkeypatch.setattr(fubon_agent, "query_start_from_report", lambda area, bank, end: d(2026, 10, 20))
    assert fubon_agent.report_query_start(account, d(2026, 10, 24)) == d(2026, 10, 20)
    monkeypatch.setattr(fubon_agent, "query_start_from_report", lambda *a: (_ for _ in ()).throw(OSError("403")))
    assert fubon_agent.report_query_start(account, d(2026, 10, 24)) == d(2026, 10, 24)  # 讀表失敗退回當天


def test_mobile_download_uses_report_start():
    from tools.local_agent import build_fubon_captcha, build_fubon_verify

    params = {"area": "台北", "resume": "download", "start_from": "sheet", "end_date": "2026-10-27"}
    cmd = build_fubon_captcha(params)
    assert "--start-from-sheet" in cmd and "--start" not in cmd and cmd[cmd.index("--end") + 1] == "2026-10-27"
    cmd = build_fubon_verify({**params, "session_token": "t", "captcha": "1234"})
    assert "--start-from-sheet" in cmd and "--start" not in cmd
    assert "--start" in build_fubon_captcha({"area": "台北", "resume": "download",
                                             "start_date": "2026-10-01", "end_date": "2026-10-27"})


def test_mobile_resolves_start_from_report(monkeypatch):
    import argparse
    from datetime import date as d

    import pytest

    from tools.bank_statement import fubon_mobile

    monkeypatch.setattr(fubon_mobile, "report_query_start", lambda account, end: d(2026, 10, 23))
    args = argparse.Namespace(start=None, end=d(2026, 10, 27), start_from_sheet=True)
    fubon_mobile._resolve_start(args, object())
    assert args.start == d(2026, 10, 23)
    with pytest.raises(ValueError):
        fubon_mobile._resolve_start(argparse.Namespace(start=None, end=d(2026, 10, 27), start_from_sheet=False), object())


def test_bank_rows_split_by_transaction_year(monkeypatch):
    """1 月初下載到的 12 月底明細，分到 12 月所屬年度（今年的列用 None＝今年財報）。"""
    from tools.bank_statement import sheet_filter as sf
    from tools.bank_statement.capture import CapturedTable

    monkeypatch.setattr(sf, "_taipei_year", lambda: 2027)
    fubon = CapturedTable(headers=list("1234567"), rows=[
        ["2027/01/02", "2027/01/02 09:00:00", "a", "", "1", "1", ""],
        ["2026/12/31", "2026/12/31 23:00:00", "b", "", "1", "1", ""],
        ["2026/12/30", "2026/12/30 10:00:00", "c", "", "1", "1", ""],
    ])
    groups = sf.split_table_by_year(fubon, "fubon")
    assert [(year, [r[2] for r in t.rows]) for year, t in groups] == [(2026, ["b", "c"]), (None, ["a"])]

    yuanta = CapturedTable(headers=list("12345678"), rows=[
        ["2026/12/31 10:41:00", "2026/12/31", "x", "", "", "1", "1", ""],
        ["", "2027/01/04", "y", "", "", "1", "1", ""],
    ])
    groups = sf.split_table_by_year(yuanta, "yuanta")
    assert [(year, [r[2] for r in t.rows]) for year, t in groups] == [(2026, ["x"]), (None, ["y"])]


def test_bank_sync_writes_each_year_to_its_report(monkeypatch):
    from tools.bank_statement import sheet_filter as sf
    from tools.bank_statement.capture import CapturedTable

    monkeypatch.setattr(sf, "_taipei_year", lambda: 2027)
    written = []
    monkeypatch.setattr(sf, "_sync_financial_report_year",
                        lambda table, area, bank, year: written.append((year, len(table.rows))) or len(table.rows))
    table = CapturedTable(headers=list("1234567"), rows=[
        ["2026/12/31", "2026/12/31 23:00:00", "b", "", "1", "1", ""],
        ["2027/01/02", "2027/01/02 09:00:00", "a", "", "1", "1", ""],
    ])
    assert sf.sync_financial_report(table, "台北", "fubon") == 2
    assert written == [(2026, 1), (None, 1)]


def test_query_start_continues_from_last_year_when_new_report_empty(monkeypatch):
    from datetime import date as d

    from tools.bank_statement import sheet_filter as sf

    monkeypatch.setattr(sf, "_taipei_year", lambda: 2027)
    last = {None: None, 2026: d(2026, 12, 29)}
    monkeypatch.setattr(sf, "_report_last_date", lambda area, bank, year: (last[year], f"富邦更新{year}"))
    assert sf.query_start_from_report("台北", "fubon", d(2027, 1, 3)) == d(2026, 12, 29)
    last[None] = d(2027, 1, 2)
    assert sf.query_start_from_report("台北", "fubon", d(2027, 1, 3)) == d(2027, 1, 2)
    last[None] = None
    last[2026] = None
    assert sf.query_start_from_report("台北", "fubon", d(2027, 1, 3)) == d(2027, 1, 1)


def test_morning_cutoff_reads_second_schedule_time(monkeypatch):
    from tools.scheduling import schedule_settings as ss

    rule = ss.parse_row([True, "fubon_statement", "x", "10:00,11:00,16:30", "每日", "", "照跑", ss.MODE_QUEUE])
    monkeypatch.setattr(ss, "get_rule", lambda *_a, **_k: rule)
    assert fsq.morning_cutoff_hour() == 11
    rule.times = ["10:00"]
    assert fsq.morning_cutoff_hour() == 24
