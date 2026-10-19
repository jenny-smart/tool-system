from pathlib import Path


def test_service_installs_periodic_offline_watchdog():
    source = Path("scripts/local_agent_service.sh").read_text(encoding="utf-8")

    assert 'WATCHDOG="$HOME/Library/Application Support/LemonToolsAgent/local-agent-watchdog.sh"' in source
    assert '"ProgramArguments": [sys.argv[3]]' in source
    assert '"StartInterval": 30' in source
    assert '/usr/bin/open -g -a Terminal "\\$LOGIN_COMMAND"' in source
