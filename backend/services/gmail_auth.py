from __future__ import annotations

import os
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CREDENTIALS_FILE = PROJECT_ROOT / "secrets" / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "secrets" / "token.json"


class GmailAuthorizationError(Exception):
    """Raised when Gmail authorization is missing, invalid, or expired."""


def _load_google_auth_dependencies():
    """Load Google OAuth dependencies only when authentication is requested."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    return Request, Credentials, InstalledAppFlow, RefreshError


def get_gmail_credentials(
    *,
    allow_interactive: bool = True,
) -> Credentials:
    """Return valid Gmail OAuth credentials."""

    Request, Credentials, InstalledAppFlow, RefreshError = (
        _load_google_auth_dependencies()
    )

    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise GmailAuthorizationError(
                "Gmail authorization has expired. Reauthorization is required."
            ) from exc

    if not credentials or not credentials.valid:
        if not allow_interactive:
            raise GmailAuthorizationError(
                "Gmail authorization is not valid. Reauthorization is required."
            )

        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                "Google OAuth credentials file is missing."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            SCOPES,
        )

        credentials = flow.run_local_server(
            port=0,
            open_browser=True,
        )

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    TOKEN_FILE.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )

    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass

    return credentials