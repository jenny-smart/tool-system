"""排程完成記行事曆：「排程設定」K 欄＋Router 完成後記 jenny@hers.com.tw。"""
import datetime as dt
from unittest.mock import MagicMock

from tools.scheduling import calendar_record as cr
from tools.scheduling import schedule_settings as ss

NOW = dt.datetime(2026, 11, 16, 7, 30, tzinfo=cr.TZ)


def _rule(key, name="名稱", times="07:00", record=""):
    return ss.parse_row([True, key, name, times, "每日", "", "照跑", ss.MODE_DIRECT, "說明", "", record])


def test_record_column_default_and_override():
    assert ss.HEADER[10] == "完成記行事曆"
    assert _rule("notify").record_calendar is True
    assert _rule("atm_unpaid", times="09:00~18:00 每30分鐘").record_calendar is False
    assert _rule("notify", record=False).record_calendar is False
    assert _rule("atm_unpaid", times="09:00~18:00 每30分鐘", record=True).record_calendar is True
    assert _rule("notify").note == "說明"
    assert ss.with_system(ss.DEFAULT_ROWS[0])[10] is True


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
