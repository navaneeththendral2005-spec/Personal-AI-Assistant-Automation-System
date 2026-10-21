import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


# Gmail read-only access.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# FRIDAY project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Local OAuth files.
CREDENTIALS_FILE = PROJECT_ROOT / "secrets" / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "secrets" / "token.json"


def get_gmail_credentials() -> Credentials:
    """
    Return valid Gmail OAuth credentials.

    If an existing token is available, reuse it and refresh it when needed.
    Otherwise, launch Google's OAuth consent flow in the browser.
    """
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"Google OAuth credentials not found: {CREDENTIALS_FILE}"
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

    # Restrict token file permissions where supported.
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")

    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        # Windows may not support Unix-style permissions.
        pass

    return credentials