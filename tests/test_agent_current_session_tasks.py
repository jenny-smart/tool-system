from pathlib import Path


def test_agent_help_treats_pre_heartbeat_tasks_as_stale():
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    help_source = source[start:end]
    assert "newest_heartbeat" in help_source
    assert 'str(task.get("started_at") or "") < newest_heartbeat' in help_source
    assert "online_agent_ids" not in help_source
