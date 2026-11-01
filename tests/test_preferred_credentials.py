"""盡量用 Jenny OAuth（jenny@lemonclean.com.tw），沒有或失效才用服務帳號。"""
from tools.common import google_auth


class _Creds:
    def __init__(self, ok=True):
        self.ok = ok

    def refresh(self, _request):
        if not self.ok:
            raise RuntimeError("invalid_grant")


def test_prefers_oauth_when_usable(monkeypatch):
    monkeypatch.setattr(google_auth, "_OAUTH_USABLE", None)
    creds = _Creds()
    monkeypatch.setattr(google_auth, "get_google_credentials", lambda: creds)
    assert google_auth.preferred_credentials(["s"], lambda: {}) is creds


def test_falls_back_to_service_account(monkeypatch):
    from google.oauth2 import service_account

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info",
                        classmethod(lambda cls, info, scopes: ("sa", info, tuple(scopes))))
    monkeypatch.setattr(google_auth, "_OAUTH_USABLE", None)
    monkeypatch.setattr(google_auth, "get_google_credentials", lambda: _Creds(ok=False))
    assert google_auth.preferred_credentials(["s"], lambda: {"a": 1}) == ("sa", {"a": 1}, ("s",))

    def missing():
        raise RuntimeError("缺少 Google OAuth 設定")

    monkeypatch.setattr(google_auth, "_OAUTH_USABLE", None)
    monkeypatch.setattr(google_auth, "get_google_credentials", missing)
    assert google_auth.preferred_credentials(["s"], {"b": 2}) == ("sa", {"b": 2}, ("s",))
