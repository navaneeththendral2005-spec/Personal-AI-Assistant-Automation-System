from fastapi import APIRouter, HTTPException, Query

from backend.services.gmail_auth import (
    CREDENTIALS_FILE,
    TOKEN_FILE,
    get_gmail_credentials,
)
from backend.services.gmail_service import (
    get_profile,
    list_emails,
)

router = APIRouter(
    prefix="/gmail",
    tags=["Gmail"],
)


@router.get("/auth/status")
def gmail_auth_status():
    """Check whether Gmail OAuth credentials are available."""

    if not CREDENTIALS_FILE.exists():
        return {
            "authorized": False,
            "message": "Google OAuth credentials file is missing.",
        }

    if not TOKEN_FILE.exists():
        return {
            "authorized": False,
            "message": "Gmail authorization has not been completed.",
        }

    try:
        credentials = get_gmail_credentials()

        return {
            "authorized": credentials.valid,
            "message": (
                "Gmail authorization is valid."
                if credentials.valid
                else "Gmail authorization is invalid."
            ),
        }

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Unable to validate Gmail authorization.",
        )


@router.get("/profile")
def gmail_profile():
    """Return the authenticated Gmail account's profile statistics."""

    try:
        return get_profile()

    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve the Gmail profile.",
        )


@router.get("/emails")
def gmail_emails(
    max_results: int = Query(default=10, ge=1, le=50),
):
    """Retrieve recent Gmail message metadata."""

    try:
        emails = list_emails(max_results=max_results)

        return {
            "count": len(emails),
            "emails": emails,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve Gmail messages.",
        )