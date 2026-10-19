from __future__ import annotations

import subprocess

from tools import local_agent
from tools import local_agent_queue as queue


def test_git_pull_command_is_fast_forward_only_from_main():
    command = local_agent.build_git_pull({})
    assert command[:2] == ["/bin/sh", "-c"]
    assert "git pull --ff-only origin main" in command[2]
    assert local_agent.build_git_pull_restart({}) == command


def test_git_pull_is_rejected_while_agent_has_active_work(monkeypatch):
    monkeypatch.setattr(queue, "ensure_task_sheet", lambda service=None, spreadsheet_id="": (object(), "sheet"))
    created = []
    monkeypatch.setattr(queue, "create_task", lambda *args, **kwargs: created.append(args))

    for status in ("running", "cancel_requested"):
        monkeypatch.setattr(
            queue,
            "list_tasks",
            lambda status=status, **kwargs: [{"task_id": "busy", "status": status}],
        )
        ok, message, task = queue.create_git_pull_task()

        assert ok is False
        assert "禁止更新" in message
        assert task is None
    assert created == []


def test_git_pull_is_queued_while_agent_is_idle(monkeypatch):
    expected = {"task_id": "pull-1", "action": "system.git_pull", "status": "pending"}
    created = []
    monkeypatch.setattr(queue, "ensure_task_sheet", lambda service=None, spreadsheet_id="": (object(), "sheet"))
    monkeypatch.setattr(queue, "list_tasks", lambda **kwargs: [])
    monkeypatch.setattr(
        queue,
        "create_task",
        lambda *args, **kwargs: created.append((args, kwargs)) or expected,
    )

    ok, message, task = queue.create_git_pull_task(created_by="tester")

    assert ok is True
    assert "Git Pull" in message
    assert task == expected
    assert created[0][0] == ("system.git_pull", {})
    assert created[0][1]["created_by"] == "tester"


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def test_failed_pull_preserves_local_repository(monkeypatch, tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    updater = tmp_path / "updater"
    _git("init", "--bare", str(remote))
    _git("init", str(seed))
    _git("-C", str(seed), "config", "user.email", "test@example.com")
    _git("-C", str(seed), "config", "user.name", "Test")
    tracked = seed / "tracked.txt"
    tracked.write_text("initial\n", encoding="utf-8")
    _git("-C", str(seed), "add", "tracked.txt")
    _git("-C", str(seed), "commit", "-m", "initial")
    _git("-C", str(seed), "branch", "-M", "main")
    _git("-C", str(seed), "remote", "add", "origin", str(remote))
    _git("-C", str(seed), "push", "-u", "origin", "main")
    _git("--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    _git("clone", str(remote), str(checkout))
    _git("clone", str(remote), str(updater))

    (updater / "tracked.txt").write_text("remote change\n", encoding="utf-8")
    _git("-C", str(updater), "config", "user.email", "test@example.com")
    _git("-C", str(updater), "config", "user.name", "Test")
    _git("-C", str(updater), "commit", "-am", "remote change")
    _git("-C", str(updater), "push")
    local_content = "important local modification\n"
    (checkout / "tracked.txt").write_text(local_content, encoding="utf-8")

    monkeypatch.setattr(local_agent, "PROJECT_ROOT", checkout)
    result = subprocess.run(
        local_agent.build_git_pull({}), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    assert result.returncode != 0
    assert (checkout / "tracked.txt").read_text(encoding="utf-8") == local_content
    assert _git("-C", str(checkout), "status", "--short").stdout == " M tracked.txt\n"


def test_pull_refuses_when_checkout_is_not_on_main(monkeypatch, tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    _git("init", "--bare", str(remote))
    _git("init", str(checkout))
    _git("-C", str(checkout), "config", "user.email", "test@example.com")
    _git("-C", str(checkout), "config", "user.name", "Test")
    (checkout / "a.txt").write_text("a\n", encoding="utf-8")
    _git("-C", str(checkout), "add", "a.txt")
    _git("-C", str(checkout), "commit", "-m", "a")
    _git("-C", str(checkout), "branch", "-M", "main")
    _git("-C", str(checkout), "remote", "add", "origin", str(remote))
    _git("-C", str(checkout), "push", "-u", "origin", "main")
    _git("-C", str(checkout), "checkout", "-b", "codex/old-branch")

    monkeypatch.setattr(local_agent, "PROJECT_ROOT", checkout)
    result = subprocess.run(local_agent.build_git_pull({}), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    assert result.returncode != 0
    assert "codex/old-branch" in result.stdout
    assert "不在 main" in result.stdout
