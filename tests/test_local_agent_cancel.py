from tools import local_agent_queue as queue


def _patch_queue(monkeypatch, task):
    changes = []
    monkeypatch.setattr(queue, "ensure_task_sheet", lambda service=None, spreadsheet_id="": (object(), "sheet"))
    monkeypatch.setattr(queue, "list_tasks", lambda **kwargs: [task])
    monkeypatch.setattr(
        queue,
        "update_task",
        lambda row_number, update, **kwargs: changes.append((row_number, update)),
    )
    return changes


def test_cancel_running_task_requests_child_termination(monkeypatch):
    changes = _patch_queue(
        monkeypatch,
        {"task_id": "task-1", "status": "running", "_row": "7"},
    )

    ok, message = queue.request_task_cancel("task-1")

    assert ok is True
    assert message == "已送出中止要求"
    assert changes == [(7, {"status": "cancel_requested", "message": "正在中止目前工作"})]


def test_cancel_pending_task_never_runs(monkeypatch):
    changes = _patch_queue(
        monkeypatch,
        {"task_id": "task-2", "status": "pending", "_row": "8"},
    )

    ok, message = queue.request_task_cancel("task-2")

    assert ok is True
    assert message == "已取消等待中的任務"
    assert changes[0][1]["status"] == "cancelled"
    assert changes[0][1]["finished_at"]


def test_completed_task_is_not_overwritten(monkeypatch):
    changes = _patch_queue(
        monkeypatch,
        {"task_id": "task-3", "status": "completed", "_row": "9"},
    )

    ok, message = queue.request_task_cancel("task-3")

    assert ok is False
    assert "completed" in message
    assert changes == []
