import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# 其他測試會把 chrome_cdp、playwright 換成假模組；這裡暫時拿掉假模組，從檔案載入真的 chrome_cdp
_saved = {name: sys.modules.pop(name) for name in list(sys.modules) if name.split(".")[0] == "playwright"}
try:
    pytest.importorskip("playwright.sync_api")
    _spec = importlib.util.spec_from_file_location(
        "_real_chrome_cdp", Path(__file__).resolve().parents[1] / "tools" / "invoice_center" / "chrome_cdp.py")
    chrome_cdp = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(chrome_cdp)
finally:
    for name in [n for n in sys.modules if n.split(".")[0] == "playwright"]:
        del sys.modules[name]
    sys.modules.update(_saved)


def test_start_agent_chrome_reuses_responsive_cdp(monkeypatch):
    run = Mock()
    monkeypatch.setattr(chrome_cdp, "_read_targets", lambda _url: [{"id": "page"}])
    monkeypatch.setattr(chrome_cdp.subprocess, "run", run)

    chrome_cdp._start_agent_chrome(chrome_cdp.DEFAULT_CDP_URL)

    run.assert_not_called()
