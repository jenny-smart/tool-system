from pathlib import Path


def test_local_agent_registers_control_actions():
    source = Path("tools/local_agent.py").read_text(encoding="utf-8")
    assert 'register_action("system.agent_status", build_agent_status)' in source
    assert 'register_action("system.agent_logs", build_agent_logs)' in source
    assert 'register_action("system.agent_restart", build_agent_restart)' in source
    assert 'register_action("system.git_pull_restart", build_git_pull_restart)' in source


def test_restart_actions_exit_child_after_success():
    source = Path("tools/local_agent.py").read_text(encoding="utf-8")
    assert 'task.get("action") in {"system.agent_restart", "system.git_pull_restart"}' in source
    assert "stop_event.set()" in source
