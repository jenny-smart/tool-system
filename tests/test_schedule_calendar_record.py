"""排程完成記行事曆：「排程設定」K 欄＋Router 完成後記 jenny@hers.com.tw。"""
import datetime as dt
from unittest.mock import MagicMock

from tools.scheduling import calendar_record as cr
from tools.scheduling import schedule_settings as ss

NOW = dt.datetime(2026, 11, 16, 7, 30, tzinfo=cr.TZ)


def _rule(key, name="名稱", times="07:00", record=""):
    return ss.parse_row([True, key, name, times, "每日", "", "照跑", ss.MODE_DIRECT, "說明", ""], record)


def test_record_column_default_and_override():
    assert _rule("notify").record_calendar is True
    assert _rule("atm_unpaid", times="09:00~18:00 每30分鐘").record_calendar is False
    assert _rule("notify", record=False).record_calendar is False
    assert _rule("atm_unpaid", times="09:00~18:00 每30分鐘", record=True).record_calendar is True
    assert _rule("notify").note == "說明"
    assert _rule("notify", record="order-system shared/x.py").record_calendar is False  # 只認 TRUE


def test_record_column_found_by_header_never_overwrites_custom_columns():
    custom = list(ss.HEADER) + ["程式限制（改時間要同步改的程式）", "排在這個時段的原因"]
    assert ss.record_col_index(custom) == (12, False)  # 加在 M 欄，不蓋 K、L
    assert ss.record_col_index(custom + ["", ss.RECORD_HEADER]) == (13, True)
    assert ss.record_col_index(list(ss.HEADER)) == (10, False)
    assert ss.record_col_index([]) == (10, False)
    assert ss.col_letter(12) == "M" and ss.col_letter(26) == "AA"
    row = ss.with_record(ss.DEFAULT_ROWS[0], 12)
    assert row[10] is None and row[11] is None and row[12] is True


def test_load_rules_reads_record_column_by_header(monkeypatch):
    header = list(ss.HEADER) + ["程式限制", "原因", ss.RECORD_HEADER]
    rows = [header,
            [True, "notify", "通知", "07:00", "每日", "", "照跑", ss.MODE_DIRECT, "", "tool-system", "TRUE", "x", False],
            [True, "gmail_401", "401", "00:00", "每月", "20", "照跑", ss.MODE_DIRECT, "", "tool-system", "說明"]]
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {"values": rows}
    monkeypatch.setattr(ss, "_service_and_id", lambda: (service, "sid"))
    monkeypatch.setattr(ss, "ensure_sheet", lambda *a, **k: False)
    rules, ok = ss.load_rules(log=lambda _t: None)
    assert ok
    by_key = {r.key: r.record_calendar for r in rules}
    assert by_key == {"notify": False, "gmail_401": True}  # K 欄「TRUE」是程式限制欄，不採用


def test_parse_title():
    assert cr.parse_title("cloud-20261116T0700-notify") == ("20261116T0700", "notify")
    assert cr.parse_title("cloud-20261116T0700-rating_complaint_daily") == ("20261116T0700", "rating_complaint_daily")
    assert cr.parse_title("cloud-router-1763247600") is None
    assert cr.parse_title("Scheduled Daily Jobs") is None


def _run(run_id, title, conclusion="success"):
    return {"id": run_id, "display_title": title, "conclusion": conclusion,
            "run_started_at": "2026-11-15T23:00:30Z", "updated_at": "2026-11-15T23:04:00Z",
            "html_url": f"https://github.com/x/runs/{run_id}"}


def test_record_creates_events_once_and_skips_disabled_rows(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "jenny-smart/tool-system")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("ORDER_SYSTEM_DISPATCH_TOKEN", "o")
    rules = [_rule("notify", "寄送日排程結果通知"), _rule("schedule_report", record=False),
             _rule("rating_complaint_daily", "官網評價非滿意→清潔客訴")]
    runs = {
        ("", "scheduled_daily.yml"): [_run(1, "cloud-20261116T0700-notify"),
                                      _run(2, "cloud-20261116T0100-schedule_report"),
                                      _run(3, "Scheduled Daily Jobs")],
        ("jenny-smart/order-system", "rating-schedule.yml"): [_run(9, "cloud-20261116T0700-rating_complaint_daily",
                                                                   "failure")],
    }
    jobs = [{"name": "run", "conclusion": "success", "steps": [
        {"name": "Checkout repository", "conclusion": "success"},
        {"name": "Run daily task", "conclusion": "success"},
        {"name": "Commit logs", "conclusion": "skipped"}]}]

    def api(path, repo="", token=""):
        if "/jobs" in path:
            return {"jobs": jobs}
        name = path.split("/actions/workflows/")[1].split("/runs")[0]
        return {"workflow_runs": runs.get((repo, name), [])}

    service = MagicMock()
    events = service.events.return_value
    events.list.return_value.execute.return_value = {"items": [
        {"extendedProperties": {"private": {"schedule_run": "jenny-smart/order-system#9"}}}]}
    count = cr.record(now=NOW, rules=rules, api=api, service=service, calendar_id="jenny@hers.com.tw",
                      log=lambda _t: None)
    assert count == 1  # notify；schedule_report K 欄關、#9 已記過、#3 不是 Router 派送的
    body = events.insert.call_args.kwargs["body"]
    assert events.insert.call_args.kwargs["calendarId"] == "jenny@hers.com.tw"
    assert body["summary"] == "【排程】寄送日排程結果通知 ✅"
    assert "✅ Run daily task" in body["description"]
    assert "Checkout" not in body["description"]
    assert "備註：說明" in body["description"]
    assert "07:00～07:04" in body["description"]
    assert body["reminders"] == {"useDefault": False, "overrides": []}
    assert body["extendedProperties"]["private"]["schedule_run"] == "jenny-smart/tool-system#1"


def test_failed_run_title_and_missing_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.delenv("ORDER_SYSTEM_DISPATCH_TOKEN", raising=False)
    rules = [_rule("rating_complaint_daily", "客訴")]
    logs = []
    assert cr.completed_runs(rules, NOW, api=lambda *a, **k: {}, log=logs.append) == []
    assert any("沒有 token" in t for t in logs)
    body = cr.build_event(_rule("notify", "通知"), _run(5, "cloud-20261116T0700-notify", "failure"),
                          "20261116T0700", "", [{"name": "run", "conclusion": "failure", "steps": [
                              {"name": "Run daily task", "conclusion": "failure"}]}])
    assert body["summary"] == "【排程】通知 ❌ 失敗"
    assert "❌ Run daily task" in body["description"]
