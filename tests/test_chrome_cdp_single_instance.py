from unittest.mock import Mock

from tools.invoice_center import chrome_cdp


def test_start_agent_chrome_reuses_responsive_cdp(monkeypatch):
    run = Mock()
    monkeypatch.setattr(chrome_cdp, "_read_targets", lambda _url: [{"id": "page"}])
    monkeypatch.setattr(chrome_cdp.subprocess, "run", run)

    chrome_cdp._start_agent_chrome(chrome_cdp.DEFAULT_CDP_URL)

    run.assert_not_called()
