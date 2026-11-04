from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tools import local_agent
from tools import local_agent_queue as queue


def test_git_pull_command_fast_forwards_from_main_then_checks_syntax():
    command = local_agent.build_git_pull({})
    assert command[:2] == ["/usr/bin/env", f"AGENT_PYTHON={sys.executable}"]
    assert command[2:4] == ["/bin/sh", "-c"]
    assert "git fetch origin main" in command[4]
    assert "git merge --ff-only FETCH_HEAD" in command[4]
    assert '"$AGENT_PYTHON" -m tools.syntax_check' in command[4]
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


def _run_pull(monkeypatch, checkout):
    monkeypatch.setattr(local_agent, "PROJECT_ROOT", checkout)
    return subprocess.run(local_agent.build_git_pull({}), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def test_pull_switches_back_to_main_and_backs_up_extra_commit(monkeypatch, tmp_path):
    """2026-11 Jenny：不在 main 時自動切回 main；有 main 沒有的 commit 先另存 backup 分支。"""
    checkout, updater = _repo_with_agent_code(tmp_path)
    _git("-C", str(checkout), "checkout", "-b", "codex/old-branch")
    (checkout / "local.txt").write_text("local\n", encoding="utf-8")
    _git("-C", str(checkout), "add", "local.txt")
    _git("-C", str(checkout), "commit", "-m", "local only")
    old = _git("-C", str(checkout), "rev-parse", "--short", "HEAD").stdout.strip()
    (updater / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git("-C", str(updater), "commit", "-am", "remote change")
    _git("-C", str(updater), "push")

    result = _run_pull(monkeypatch, checkout)

    assert result.returncode == 0, result.stdout
    assert "codex/old-branch" in result.stdout and "自動切回 main" in result.stdout
    assert _git("-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert (checkout / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert _git("-C", str(checkout), "rev-parse", "--short", f"backup-{old}").stdout.strip() == old


def test_pull_switches_detached_head_without_backup_when_already_in_main(monkeypatch, tmp_path):
    checkout, updater = _repo_with_agent_code(tmp_path)
    _git("-C", str(checkout), "checkout", "--detach")
    (updater / "app.py").write_text("x = 3\n", encoding="utf-8")
    _git("-C", str(updater), "commit", "-am", "remote change")
    _git("-C", str(updater), "push")

    result = _run_pull(monkeypatch, checkout)

    assert result.returncode == 0, result.stdout
    assert "另存為分支" not in result.stdout
    assert _git("-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert (checkout / "app.py").read_text(encoding="utf-8") == "x = 3\n"


def test_pull_refuses_to_switch_when_not_on_main_with_local_changes(monkeypatch, tmp_path):
    checkout, _updater = _repo_with_agent_code(tmp_path)
    _git("-C", str(checkout), "checkout", "-b", "codex/old-branch")
    (checkout / "app.py").write_text("x = 99\n", encoding="utf-8")

    result = _run_pull(monkeypatch, checkout)

    assert result.returncode != 0
    assert "未存檔的修改" in result.stdout and "app.py" in result.stdout
    assert _git("-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "codex/old-branch"
    assert (checkout / "app.py").read_text(encoding="utf-8") == "x = 99\n"


def _repo_with_agent_code(tmp_path):
    """建立 remote/checkout/updater 三份 repo，內含 tools/syntax_check.py。"""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git("init", "--bare", str(remote))
    _git("init", str(seed))
    _git("-C", str(seed), "config", "user.email", "test@example.com")
    _git("-C", str(seed), "config", "user.name", "Test")
    (seed / "tools").mkdir()
    (seed / "tools" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(Path(local_agent.__file__).with_name("syntax_check.py"), seed / "tools" / "syntax_check.py")
    (seed / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git("-C", str(seed), "add", ".")
    _git("-C", str(seed), "commit", "-m", "initial")
    _git("-C", str(seed), "branch", "-M", "main")
    _git("-C", str(seed), "remote", "add", "origin", str(remote))
    _git("-C", str(seed), "push", "-u", "origin", "main")
    _git("--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    repos = []
    for name in ("checkout", "updater"):
        path = tmp_path / name
        _git("clone", str(remote), str(path))
        _git("-C", str(path), "config", "user.email", "test@example.com")
        _git("-C", str(path), "config", "user.name", "Test")
        repos.append(path)
    return repos


def test_pull_with_syntax_error_rolls_back(monkeypatch, tmp_path):
    checkout, updater = _repo_with_agent_code(tmp_path)
    before = _git("-C", str(checkout), "rev-parse", "HEAD").stdout
    (updater / "app.py").write_text("x = (\n", encoding="utf-8")
    _git("-C", str(updater), "commit", "-am", "broken")
    _git("-C", str(updater), "push")

    monkeypatch.setattr(local_agent, "PROJECT_ROOT", checkout)
    result = subprocess.run(local_agent.build_git_pull({}), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    assert result.returncode != 0
    assert "語法錯誤" in result.stdout
    assert _git("-C", str(checkout), "rev-parse", "HEAD").stdout == before
    assert (checkout / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def test_pull_merges_when_local_has_extra_commit(monkeypatch, tmp_path):
    checkout, updater = _repo_with_agent_code(tmp_path)
    (checkout / "local.txt").write_text("local\n", encoding="utf-8")
    _git("-C", str(checkout), "add", "local.txt")
    _git("-C", str(checkout), "commit", "-m", "local log")
    (updater / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git("-C", str(updater), "commit", "-am", "remote change")
    _git("-C", str(updater), "push")

    monkeypatch.setattr(local_agent, "PROJECT_ROOT", checkout)
    result = subprocess.run(local_agent.build_git_pull({}), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    assert result.returncode == 0, result.stdout
    assert "更新後" in result.stdout
    assert (checkout / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (checkout / "local.txt").exists()
