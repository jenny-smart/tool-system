from tools import local_agent_recovery


def test_recovery_finishes_running_and_cancel_requested_tasks(monkeypatch):
    tasks = [
        {"_row": "2", "status": "running", "agent_id": "agent"},
        {"_row": "3", "status": "cancel_requested", "agent_id": "agent"},
        {"_row": "4", "status": "completed", "agent_id": "agent"},
    ]
    updates = []

    monkeypatch.setattr(local_agent_recovery, "get_sheets_service", lambda: object())
    monkeypatch.setattr(local_agent_recovery, "get_master_spreadsheet_id", lambda: "sheet")
    requested_limits = []

    def list_tasks(**kwargs):
        requested_limits.append(kwargs["limit"])
        return tasks

    monkeypatch.setattr(local_agent_recovery, "list_tasks", list_tasks)
    monkeypatch.setattr(
        local_agent_recovery,
        "update_task",
        lambda row, values, **_kwargs: updates.append((row, values)),
    )

    assert local_agent_recovery.recover_interrupted_tasks("agent") == 2
    assert requested_limits == [5000]
    assert [row for row, _values in updates] == [2, 3]
    assert all(values["status"] == "failed" for _row, values in updates)
