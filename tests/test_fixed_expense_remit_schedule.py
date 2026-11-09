from datetime import date

from tools.finance_management import fixed_expense_transfer, taichung_fixed_expense
from tools.scheduled_finance import runner
from tools.scheduling import schedule_settings
from tools import local_agent_queue


def test_schedule_skips_kaohsiung(monkeypatch):
    status = [
        {"area": "台中", "total": 10000, "memo": "", "fixed": False, "self_remit": False, "done": False, "detail": "待匯款"},
        {"area": "高雄", "total": 40000, "memo": "", "fixed": True, "self_remit": False, "done": False, "detail": "待匯款"},
    ]
    periods = []
    monkeypatch.setattr(fixed_expense_transfer, "remit_status", lambda period: periods.append(period) or status)
    monkeypatch.setattr(taichung_fixed_expense, "taichung_status", lambda period: {"applicable": False, "missing": []})
    monkeypatch.setattr(schedule_settings, "get_mode", lambda key, default: schedule_settings.MODE_QUEUE)
    queued, months = [], []
    monkeypatch.setattr(local_agent_queue, "create_task",
                        lambda action, params, created_by="": queued.append(params["area"]) or months.append(params["month"]) or {"task_id": "T"})
    reminders = []
    monkeypatch.setattr(runner, "remind", lambda title, body, prefix: reminders.append(body))
    assert runner.run_fixed_expense_remit(date(2026, 11, 10)) == 0
    assert queued == ["台中"]
    assert periods == ["202610"] and months == ["202610"]  # 10 日匯上個月那欄
    assert "高雄" not in reminders[0]


def test_agent_command_allow_posted():
    from tools import local_agent

    base = {"area": "台中", "month": "202611", "selected_rows": [41, 47]}
    assert "--allow-posted" not in local_agent.build_fubon_fixed_expense_transfer(base)
    assert "--allow-posted" in local_agent.build_fubon_fixed_expense_transfer(dict(base, allow_posted=True))
