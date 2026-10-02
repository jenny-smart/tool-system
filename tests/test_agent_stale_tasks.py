from pathlib import Path


def test_agent_help_retains_tasks_with_unconfirmed_owners():
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    help_source = source[start:end]

    assert "online_agent_ids" in help_source
    assert "unmatched_running_tasks" in help_source
    assert "忽略舊的未收尾工作" not in help_source
    assert "選擇要中止的工作" in help_source
