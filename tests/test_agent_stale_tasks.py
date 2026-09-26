from pathlib import Path


def test_agent_help_ignores_stale_tasks_from_old_agent_sessions():
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    help_source = source[start:end]

    assert "online_agent_ids" in help_source
    assert "stale_running_tasks" in help_source
    assert "忽略舊的未收尾工作" in help_source
