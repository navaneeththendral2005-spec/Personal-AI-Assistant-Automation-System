"""Read-only Gmail API integration for FRIDAY.

Gmail messages may expose their content as plain text, HTML, nested MIME parts,
or (occasionally) HTML content incorrectly labelled as ``text/plain``. This
module normalizes those shapes to readable text before a message is stored.
"""
from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable, Iterator
from typing import Any

from bs4 import BeautifulSoup


def get_gmail_service():
    """Create an authenticated Gmail API client (imports Google SDK lazily)."""
    # Keep parsing helpers importable in unit-test and offline contexts where
    # the optional Gmail SDK/OAuth dependencies are not installed.
    from googleapiclient.discovery import build

    from backend.services.gmail_auth import get_gmail_credentials

    credentials = get_gmail_credentials()
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def get_profile() -> dict[str, Any]:
    """Retrieve the authenticated Gmail account's profile."""
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    return {
        "email_address": profile.get("emailAddress"),
        "messages_total": profile.get("messagesTotal", 0),
        "threads_total": profile.get("threadsTotal", 0),
    }


def _decode_body(data: str | None) -> str:
    """Decode a Gmail base64url-encoded message body safely."""
    if not data or not isinstance(data, str):
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        # Most Gmail mail is UTF-8, but real messages can use legacy charsets.
        # UTF-8 replacement decoding is predictable and never raises here.
        return decoded.decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _clean_plain_text(text: str) -> str:
    """Normalize whitespace and invisible formatting characters safely."""
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Email templates commonly contain BOM/zero-width characters and figure
    # spaces from layout tables. They are not useful in a plain-text body.
    text = "".join(
        " " if unicodedata.category(char) == "Zs" else char
        for char in text
        if char not in _INVISIBLE_FORMAT_CHARS
    )

    # Normalize each line without flattening the email's paragraph structure.
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_HTML_ROOT_RE = re.compile(r"(?is)^\s*(?:<!doctype\s+html\b|<html\b|<head\b|<body\b)")
_HTML_TAG_RE = re.compile(
    r"(?is)</?(?:html|head|body|div|p|table|thead|tbody|tr|td|th|span|br|hr|"
    r"style|script|meta|link|a|img|ul|ol|li|h[1-6]|section|article)\b[^>]*>"
)


def _looks_like_html(text: str | None) -> bool:
    """Detect full HTML documents and common HTML fragments in body content."""
    if not text or not text.strip():
        return False
    if _HTML_ROOT_RE.search(text):
        return True
    # A single angle-bracket expression can be ordinary prose/code. Require
    # multiple recognizable tags for fragments unless the document has a root.
    return len(_HTML_TAG_RE.findall(text[:20_000])) >= 2


_INVISIBLE_FORMAT_CHARS = {"\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"}


def _needs_body_repair(text: str | None) -> bool:
    """Identify stored bodies needing HTML conversion or whitespace cleanup."""
    if not text:
        return False
    if _looks_like_html(text):
        return True
    return any(
        char in _INVISIBLE_FORMAT_CHARS
        or (unicodedata.category(char) == "Zs" and char != " ")
        for char in text
    )


def _html_to_text(html: str) -> str:
    """Convert HTML email content into readable plain text."""
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for element in soup(
        [
            "script", "style", "head", "title", "meta", "link", "iframe",
            "object", "embed", "svg", "canvas", "noscript", "template",
        ]
    ):
        element.decompose()

    # Remove only clearly invisible tracking images; preserve meaningful alt text.
    for image_tag in soup.find_all("img"):
        width = str(image_tag.get("width", "")).strip().lower()
        height = str(image_tag.get("height", "")).strip().lower()
        style = re.sub(r"\s+", "", str(image_tag.get("style", "")).lower())
        is_pixel = (
            width in {"0", "1", "1px"}
            or height in {"0", "1", "1px"}
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if is_pixel:
            image_tag.decompose()
        else:
            alt_text = image_tag.get("alt", "").strip()
            image_tag.replace_with(f" {alt_text} " if alt_text else " ")

    # Use a non-whitespace marker: BeautifulSoup's strip=True would discard
    # standalone newline strings inserted directly into the document.
    line_break = "\ue000FRIDAY_LINE_BREAK\ue001"
    block_tags = {
        "address", "article", "blockquote", "div", "footer", "header",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "ol", "p", "section",
        "table", "tbody", "td", "th", "thead", "tr", "ul",
    }
    for tag in soup.find_all(list(block_tags) + ["br", "hr"]):
        if tag.name in {"br", "hr"}:
            tag.replace_with(line_break)
        else:
            tag.insert_before(line_break)
            tag.insert_after(line_break)
            if tag.name == "li":
                tag.insert_before("- ")

    text = soup.get_text(separator=" ", strip=True).replace(line_break, "\n")
    text = _clean_plain_text(text)
    # Keep punctuation attached to the word before it after inline HTML tags.
    text = re.sub(r"[ \t]+([,.;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    return text


def _iter_mime_leaves(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield MIME leaf parts recursively, including nested multipart messages."""
    parts = payload.get("parts")
    if isinstance(parts, list) and parts:
        for part in parts:
            if isinstance(part, dict):
                yield from _iter_mime_leaves(part)
        return
    yield payload


def _part_text(
    part: dict[str, Any],
    attachment_loader: Callable[[str], str | None] | None = None,
) -> str:
    """Decode a MIME leaf, optionally loading content stored as a Gmail attachment."""
    body = part.get("body", {})
    if not isinstance(body, dict):
        return ""

    data = body.get("data")
    if not data and body.get("attachmentId") and attachment_loader is not None:
        try:
            data = attachment_loader(str(body["attachmentId"]))
        except Exception:
            # One unavailable part should not prevent fallback to another MIME
            # alternative. The caller still receives any usable text part.
            data = None
    return _decode_body(data)


def _extract_plain_text(
    payload: dict[str, Any],
    attachment_loader: Callable[[str], str | None] | None = None,
) -> str:
    """Extract readable text from Gmail MIME payloads, preferring plain text.

    Some senders mislabel HTML markup as ``text/plain``. Detect and convert that
    case too, which prevents raw ``<!DOCTYPE html>`` documents reaching storage.
    """
    if not isinstance(payload, dict):
        return ""

    leaves = list(_iter_mime_leaves(payload))
    plain_parts = [
        part for part in leaves
        if str(part.get("mimeType", "")).lower() == "text/plain"
    ]
    html_parts = [
        part for part in leaves
        if str(part.get("mimeType", "")).lower() == "text/html"
    ]

    for part in plain_parts:
        decoded = _part_text(part, attachment_loader)
        if not decoded.strip():
            continue
        text = _html_to_text(decoded) if _looks_like_html(decoded) else _clean_plain_text(decoded)
        if text:
            return text

    for part in html_parts:
        decoded = _part_text(part, attachment_loader)
        if decoded.strip():
            text = _html_to_text(decoded)
            if text:
                return text

    # A few malformed messages have content data but no useful MIME type.
    for part in leaves:
        if str(part.get("mimeType", "")).lower() in {"text/plain", "text/html"}:
            continue
        decoded = _part_text(part, attachment_loader)
        if decoded.strip() and _looks_like_html(decoded):
            text = _html_to_text(decoded)
            if text:
                return text

    return ""


def _message_to_email(details: dict[str, Any], service) -> dict[str, Any]:
    """Map one full Gmail API response into FRIDAY's normalized service shape."""
    message_id = details.get("id")
    payload = details.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    headers = payload.get("headers", [])
    if not isinstance(headers, list):
        headers = []

    header_map = {
        str(header["name"]).lower(): str(header["value"])
        for header in headers
        if isinstance(header, dict) and "name" in header and "value" in header
    }

    def load_attachment(attachment_id: str) -> str | None:
        if not message_id:
            return None
        response = (
            service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
        )
        return response.get("data")

    body_text = _extract_plain_text(payload, attachment_loader=load_attachment)
    return {
        "id": message_id,
        "thread_id": details.get("threadId"),
        "sender": header_map.get("from"),
        "recipient": header_map.get("to"),
        "subject": header_map.get("subject"),
        "date": header_map.get("date"),
        "snippet": details.get("snippet", ""),
        "body_text": body_text,
        "labels": details.get("labelIds", []),
    }


def get_email_by_id(message_id: str, *, service=None) -> dict[str, Any]:
    """Fetch one message by Gmail ID using read-only GET operations."""
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("A valid Gmail message ID is required.")
    service = service or get_gmail_service()
    details = (
        service.users().messages().get(
            userId="me", id=message_id.strip(), format="full"
        ).execute()
    )
    return _message_to_email(details, service)


def list_emails_page(
    max_results: int = 50,
    page_token: str | None = None,
    *,
    service=None,
) -> dict[str, Any]:
    """Fetch one Gmail messages page and its continuation token.

    Gmail's API permits at most 50 messages per page here. The injected service
    parameter keeps pagination testable without OAuth or network access.
    This function only uses read-only Gmail API methods.
    """
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50")

    service = service or get_gmail_service()
    request_args: dict[str, Any] = {"userId": "me", "maxResults": max_results}
    if page_token:
        request_args["pageToken"] = page_token
    response = service.users().messages().list(**request_args).execute()

    emails = []
    for message in response.get("messages", []):
        message_id = message.get("id") if isinstance(message, dict) else None
        if not message_id:
            continue
        details = (
            service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        )
        emails.append(_message_to_email(details, service))

    return {
        "emails": emails,
        "next_page_token": response.get("nextPageToken"),
    }


def list_emails(
    max_results: int = 10,
    page_token: str | None = None,
    *,
    service=None,
) -> list[dict[str, Any]]:
    """Retrieve one page of Gmail messages with readable text bodies.

    Pass ``page_token`` from a prior page to continue listing. Each call is
    limited to 50 messages. Existing callers retain the original list return
    shape; callers needing the continuation token can use ``list_emails_page``.
    Gmail messages are never modified.
    """
    return list_emails_page(
        max_results=max_results,
        page_token=page_token,
        service=service,
    )["emails"]
