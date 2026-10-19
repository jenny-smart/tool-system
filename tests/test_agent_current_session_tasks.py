from pathlib import Path


def test_agent_help_does_not_treat_heartbeat_as_session_start():
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    help_source = source[start:end]
    assert "newest_heartbeat" not in help_source
    assert "忽略舊的未收尾工作" not in help_source
    assert "選擇要中止的工作" in help_source
