"""系統通知事件一律建在 Jenny 的行事曆，不用共用日曆、不寄邀請信。"""
from tools.orders_system import calendar_notify


class _Events:
    def __init__(self, calls):
        self.calls = calls

    def insert(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        return {"id": "e"}


class _Service:
    def __init__(self):
        self.calls = []

    def events(self):
        return _Events(self.calls)


def test_default_calendar_is_jenny_without_invite(monkeypatch):
    service = _Service()
    monkeypatch.setattr(calendar_notify, "_calendar_service", lambda *_a: service)
    calendar_notify.create_notify_event(summary="ATM對帳完成")
    call = service.calls[0]
    assert call["calendarId"] in ("jenny@hers.com.tw", "jenny@lemonclean.com.tw")
    assert call["sendUpdates"] == "none" and "attendees" not in call["body"]
    assert [r["minutes"] for r in call["body"]["reminders"]["overrides"]] == [10, 20]


def test_empty_reminders_means_no_popup(monkeypatch):
    service = _Service()
    monkeypatch.setattr(calendar_notify, "_calendar_service", lambda *_a: service)
    calendar_notify.create_notify_event(summary="紀錄", reminder_minutes=[])
    assert service.calls[0]["body"]["reminders"]["overrides"] == []


def test_calendar_uses_jenny_oauth_when_refresh_token_set(monkeypatch):
    values = {
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "rt",
        "GOOGLE_OAUTH_CLIENT_ID": "cid",
        "GOOGLE_OAUTH_CLIENT_SECRET": "cs",
    }
    monkeypatch.setattr("tools.common.google_auth._oauth_value", lambda name: values.get(name, ""))
    creds = calendar_notify._oauth_credentials()
    assert creds.refresh_token == "rt"
    assert creds.client_id == "cid"


def test_calendar_falls_back_to_service_account_without_token(monkeypatch):
    monkeypatch.setattr("tools.common.google_auth._oauth_value", lambda name: "")
    assert calendar_notify._oauth_credentials() is None


def test_calendar_client_override_matches_refresh_token_client(monkeypatch):
    values = {
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "rt",
        "GOOGLE_CALENDAR_CLIENT_ID": "cal-id",
        "GOOGLE_CALENDAR_CLIENT_SECRET": "cal-secret",
        "GOOGLE_OAUTH_CLIENT_ID": "drive-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "drive-secret",
    }
    monkeypatch.setattr("tools.common.google_auth._oauth_value", lambda name: values.get(name, ""))
    creds = calendar_notify._oauth_credentials()
    assert (creds.client_id, creds.client_secret) == ("cal-id", "cal-secret")


def test_hers_calendar_falls_back_to_lemonclean_without_token(monkeypatch):
    from tools.common import google_auth

    monkeypatch.setattr(calendar_notify, "_calendar_service", lambda *_a: "svc")
    monkeypatch.setattr(google_auth, "_oauth_value", lambda name: "")
    assert calendar_notify.calendar_for() == ("svc", "jenny@lemonclean.com.tw")
    monkeypatch.setattr(google_auth, "_oauth_value", lambda name: "tok" if name == "GOOGLE_HERS_CALENDAR_REFRESH_TOKEN" else "")
    assert calendar_notify.calendar_for() == ("svc", "jenny@hers.com.tw")


def test_replace_prefix_deletes_same_day_events_before_insert(monkeypatch):
    from datetime import datetime

    log = []

    class Req:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class Events:
        def list(self, **kw):
            log.append(("list", kw["q"]))
            return Req({"items": [{"id": "old", "summary": "清潔異動未備妥：2 筆"},
                                  {"id": "other", "summary": "別的提醒"}]})

        def delete(self, calendarId, eventId):
            log.append(("delete", eventId))
            return Req({})

        def insert(self, **kw):
            log.append(("insert", kw["body"]["summary"]))
            return Req({"id": "new"})

    class Svc:
        def events(self):
            return Events()

    monkeypatch.setattr(calendar_notify, "calendar_for", lambda cid=None: (Svc(), "jenny@hers.com.tw"))
    calendar_notify.create_notify_event(summary="清潔異動未備妥：1 筆", start=datetime(2026, 11, 2, 10, 30,
                                        tzinfo=calendar_notify.TAIPEI_TZ), replace_prefix="清潔異動未備妥")
    assert log == [("list", "清潔異動未備妥"), ("delete", "old"), ("insert", "清潔異動未備妥：1 筆")]
