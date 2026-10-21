from typing import Any

from googleapiclient.discovery import build

from backend.services.gmail_auth import get_gmail_credentials


def get_gmail_service():
    """Create an authenticated Gmail API client."""
    credentials = get_gmail_credentials()

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


def get_profile() -> dict[str, Any]:
    """Retrieve the authenticated Gmail account's profile."""
    service = get_gmail_service()

    profile = service.users().getProfile(userId="me").execute()

    return {
        "email_address": profile.get("emailAddress"),
        "messages_total": profile.get("messagesTotal", 0),
        "threads_total": profile.get("threadsTotal", 0),
    }


def list_emails(max_results: int = 10) -> list[dict[str, Any]]:
    """
    Retrieve recent Gmail messages.

    Returns basic metadata and plain-text content where available.
    Does not send, delete, or modify emails.
    """
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50")

    service = get_gmail_service()

    response = (
        service.users()
        .messages()
        .list(
            userId="me",
            maxResults=max_results,
        )
        .execute()
    )

    messages = response.get("messages", [])
    emails = []

    for message in messages:
        details = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message["id"],
                format="full",
            )
            .execute()
        )

        headers = details.get("payload", {}).get("headers", [])

        header_map = {
            header["name"].lower(): header["value"]
            for header in headers
        }

        emails.append(
            {
                "id": details["id"],
                "thread_id": details.get("threadId"),
                "sender": header_map.get("from"),
                "recipient": header_map.get("to"),
                "subject": header_map.get("subject"),
                "date": header_map.get("date"),
                "snippet": details.get("snippet", ""),
                "labels": details.get("labelIds", []),
            }
        )

    return emails