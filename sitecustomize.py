from __future__ import annotations

import os


def _oauth_env_ready() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
        )
    )


if _oauth_env_ready():
    try:
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials as UserCredentials

        _original_from_service_account_info = service_account.Credentials.from_service_account_info

        @classmethod
        def _from_service_account_info(cls, info, *args, **kwargs):
            scopes = kwargs.get("scopes")
            if scopes is None and args:
                scopes = args[0]

            return UserCredentials(
                token=None,
                refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"].strip(),
                client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
                scopes=scopes,
            )

        service_account.Credentials.from_service_account_info = _from_service_account_info
    except Exception:
        # Do not block Python startup. The existing service-account path remains
        # available when OAuth variables are absent or a dependency is missing.
        pass
