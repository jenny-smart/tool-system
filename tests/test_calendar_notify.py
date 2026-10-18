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
    monkeypatch.setattr(calendar_notify, "_calendar_service", lambda: service)
    calendar_notify.create_notify_event(summary="ATM對帳完成")
    call = service.calls[0]
    assert call["calendarId"] == "jenny@lemonclean.com.tw"
    assert call["sendUpdates"] == "none" and "attendees" not in call["body"]
    assert [r["minutes"] for r in call["body"]["reminders"]["overrides"]] == [10, 20]


def test_empty_reminders_means_no_popup(monkeypatch):
    service = _Service()
    monkeypatch.setattr(calendar_notify, "_calendar_service", lambda: service)
    calendar_notify.create_notify_event(summary="紀錄", reminder_minutes=[])
    assert service.calls[0]["body"]["reminders"]["overrides"] == []
