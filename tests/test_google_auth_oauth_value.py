import sys
import types

from tools.common import google_auth


def test_oauth_value_reads_streamlit_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    monkeypatch.setenv("TOOLS_APP_SECRETS_FILE", str(tmp_path / "none.toml"))
    monkeypatch.setattr(google_auth.Path, "home", staticmethod(lambda: tmp_path))
    fake = types.SimpleNamespace(secrets={"GOOGLE_CALENDAR_CLIENT_ID": " cal-id "})
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    assert google_auth._oauth_value("GOOGLE_CALENDAR_CLIENT_ID") == "cal-id"
    assert google_auth._oauth_value("MISSING") == ""


def test_env_wins_over_streamlit_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "env-id")
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={"GOOGLE_CALENDAR_CLIENT_ID": "st-id"}))
    assert google_auth._oauth_value("GOOGLE_CALENDAR_CLIENT_ID") == "env-id"
