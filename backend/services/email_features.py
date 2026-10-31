"""Shared, leakage-safe feature extraction for FRIDAY's priority engine.

This module is the single definition of "what the model may look at". Training,
offline evaluation, and any future inference path must all call it, so a feature
can never mean one thing at training time and another at prediction time.

Design rules
------------
* Pure Python, no scikit-learn, no database access, no network.
* Only an explicit allow-list of columns is ever read (``ALLOWED_INPUT_COLUMNS``).
  Ground-truth (``true_*``), adjudication (``final_*``), and prediction
  (``rules_*``, ``predicted_*``, ``llm_*``) columns can never influence a feature,
  even when they are present in the same CSV row.
* Gmail's own labels are Google's predictions (and some reflect human behaviour).
  The default ``independent`` mode ignores them completely so headline metrics
  measure FRIDAY, not Google. ``gmail_assisted`` mode allows a small, task-specific
  allow-list and is intended for ablation studies only.
* Missing, empty, malformed, or inconsistent metadata never raises. It is mapped to
  neutral values plus explicit ``*_missing`` / ``*_invalid`` flags so a model can
  tell "unknown" from "none".
* Every feature is a finite float and the feature order is fixed
  (``FEATURE_NAMES``).
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Iterable, Mapping

FEATURE_SCHEMA_VERSION = "1.0"

# Body text longer than this is truncated *for features and model text only*.
MAX_BODY_CHARS = 20_000
# Keyword scans only look at the start of the body, where intent usually lives.
KEYWORD_SCAN_BODY_CHARS = 2_000

TASKS = ("priority", "is_spam", "needs_response", "category")
GMAIL_LABEL_MODES = ("independent", "gmail_assisted")

# --------------------------------------------------------------------------- #
# Leakage policy
# --------------------------------------------------------------------------- #

# The only columns a feature extractor may read.
ALLOWED_INPUT_COLUMNS: tuple[str, ...] = (
    "sender",
    "subject",
    "snippet",
    "body_text",
    "labels",
    "recipient",
    "message_date",
    "date",
    "received_at",
    "thread_id",
    "thread_message_count",
    "in_reply_to",
    "references",
    "has_attachment",
    "attachment_count",
    "attachment_names",
    "attachment_mime_types",
)

# Column-name prefixes that hold ground truth, human decisions, or model output.
FORBIDDEN_COLUMN_PREFIXES: tuple[str, ...] = (
    "true_",
    "final_",
    "predicted_",
    "rules_",
    "llm_",
    "review",
    "needs_review",
    "label_source",
    "adjudicat",
    "decision_",
)


class FeatureLeakageError(ValueError):
    """Raised when a forbidden column would reach a model input."""


def find_forbidden_columns(columns: Iterable[str]) -> list[str]:
    """Return the column names that must never become model inputs."""
    return [
        column
        for column in columns
        if isinstance(column, str)
        and column.strip().lower().startswith(FORBIDDEN_COLUMN_PREFIXES)
    ]


def assert_no_forbidden_columns(columns: Iterable[str]) -> None:
    """Raise ``FeatureLeakageError`` if any forbidden column is present."""
    offending = find_forbidden_columns(columns)
    if offending:
        raise FeatureLeakageError(
            "Forbidden columns would leak labels or predictions into model "
            f"inputs: {sorted(offending)}"
        )


def model_input_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return only allow-listed fields of ``row``.

    This is the *only* object feature code reads, so truth, adjudication, and
    prediction columns are structurally unable to influence a feature.
    """
    if not isinstance(row, Mapping):
        return {}
    return {key: row[key] for key in ALLOWED_INPUT_COLUMNS if key in row}


# Gmail system labels (Google's own signals plus user-behaviour state).
GOOGLE_CLASSIFIER_LABELS = frozenset({
    "SPAM",
    "IMPORTANT",
    "INBOX",
    "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
})
# Labels that record what the human did (or will do) with a message. They can
# leak the very judgement the model is supposed to predict, so they are never
# usable in any mode. Custom user labels (e.g. "Jobs") are excluded for the same
# reason: they are manual organisation.
NEVER_SAFE_GMAIL_LABELS = frozenset({
    "STARRED", "UNREAD", "SENT", "DRAFT", "TRASH", "CHAT", "SNOOZED",
})
KNOWN_SYSTEM_LABELS = GOOGLE_CLASSIFIER_LABELS | NEVER_SAFE_GMAIL_LABELS

# Task-specific allow-lists for ``gmail_assisted`` mode (ablation only).
ASSISTED_LABELS_BY_TASK: dict[str, frozenset[str]] = {
    "is_spam": frozenset({
        "SPAM", "INBOX", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
        "CATEGORY_UPDATES", "CATEGORY_FORUMS", "CATEGORY_PERSONAL",
    }),
    "needs_response": frozenset({
        "IMPORTANT", "INBOX", "CATEGORY_PERSONAL", "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
    }),
    "priority": frozenset({
        "IMPORTANT", "INBOX", "CATEGORY_PERSONAL", "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
    }),
    "category": frozenset({
        "CATEGORY_PERSONAL", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
        "CATEGORY_UPDATES", "CATEGORY_FORUMS",
    }),
}

# Fixed one-hot columns (kept identical in every mode so the schema never changes).
LABEL_FEATURE_ORDER: tuple[str, ...] = tuple(sorted(GOOGLE_CLASSIFIER_LABELS))


def safe_gmail_labels(
    labels: Iterable[str],
    task: str | None,
    mode: str = "independent",
) -> frozenset[str]:
    """Return the subset of Gmail labels that is safe to use for ``task``.

    ``independent`` mode always returns an empty set. ``gmail_assisted`` mode
    returns only Google's classifier labels listed for the task; behaviour labels
    and custom user labels are always dropped.
    """
    if mode not in GMAIL_LABEL_MODES:
        raise ValueError(
            f"Unknown Gmail label mode {mode!r}; expected one of {GMAIL_LABEL_MODES}"
        )
    if mode == "independent":
        return frozenset()
    allowed = (
        ASSISTED_LABELS_BY_TASK[task]
        if task in ASSISTED_LABELS_BY_TASK
        else GOOGLE_CLASSIFIER_LABELS
    )
    return frozenset(label for label in labels if label in allowed)


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #

_CONTROL_AND_INVISIBLE = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u00ad\u034f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)
_WHITESPACE = re.compile(r"\s+")


def _as_text(value: Any) -> str:
    """Convert any input to ``str`` without ever raising."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return ""


def clean_text(value: Any, max_chars: int | None = None) -> str:
    """Return normalised text: unescaped, NFKC, invisible chars removed, trimmed.

    ``None``, empty strings, whitespace-only strings, NaN, and non-strings all
    normalise to ``""`` (or their string form for non-strings).
    """
    text = _as_text(value)
    if not text:
        return ""
    # Gmail snippets arrive HTML-escaped (e.g. "&#39;"); handle double escaping.
    for _ in range(2):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_AND_INVISIBLE.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
    return text


# ---- sender ---------------------------------------------------------------- #

_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_VALID_ADDRESS = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
_TWO_LEVEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "ac.in", "gov.in", "net.in",
    "org.in", "com.au", "net.au", "org.au", "co.jp", "co.nz", "com.br", "com.sg",
    "co.za", "com.mx", "com.tr", "com.cn",
})
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "rediffmail.com", "aol.com", "zoho.com",
})
_NOREPLY_LOCAL = re.compile(r"(?:^|[._\-])(?:no[._\-]?reply|do[._\-]?not[._\-]?reply|donotreply)(?:$|[._\-\d])")
_BULK_LOCAL = re.compile(
    r"^(?:newsletter|news|mailer|marketing|promo|promotions?|offers?|deals?|"
    r"notifications?|notify|alerts?|updates?|info|hello|team|support|billing|"
    r"invoices?|receipts?|jobs?|careers?|digest|bounces?|community|events?)$"
)
_BULK_DOMAIN_FIRST_LABELS = frozenset({
    "mail", "email", "e", "em", "news", "newsletter", "notify", "notifications",
    "mg", "mailer", "bounce", "bounces", "marketing", "click", "send", "smtp",
    "info", "t", "go",
})
_ESP_REGISTERED_DOMAINS = frozenset({
    "sendgrid.net", "mailchimp.com", "mandrillapp.com", "amazonses.com",
    "mailgun.org", "sparkpostmail.com", "constantcontact.com", "hubspotemail.net",
    "rsgsv.net", "mcsv.net", "createsend.com", "cmail19.com", "cmail20.com",
})


@dataclass(frozen=True)
class SenderInfo:
    """Normalised view of a ``From`` header. ``is_valid`` is False when no usable
    address could be recovered (malformed, empty, or missing)."""

    display_name: str
    address: str
    local_part: str
    domain: str
    registered_domain: str
    is_valid: bool
    address_count: int


_EMPTY_SENDER = SenderInfo("", "", "", "", "", False, 0)


def registered_domain(domain: str) -> str:
    """Cheap registrable-domain heuristic (no external public-suffix list)."""
    labels = [label for label in domain.lower().strip(".").split(".") if label]
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return ".".join(labels)


def parse_sender(value: Any) -> SenderInfo:
    """Parse a ``From`` value robustly. Never raises."""
    raw = clean_text(value)
    if not raw:
        return _EMPTY_SENDER
    try:
        pairs = [(name, addr) for name, addr in getaddresses([raw]) if name or addr]
    except Exception:  # pragma: no cover - getaddresses is very forgiving
        pairs = []

    chosen_name, chosen_address = "", ""
    for name, address in pairs:
        if "@" in address:
            chosen_name, chosen_address = name, address
            break
    if not chosen_address:
        match = _EMAIL_IN_TEXT.search(raw)
        if match:
            chosen_address = match.group(0)
            chosen_name = raw.replace(chosen_address, "").strip(" <>\"'(),;")
        elif pairs:
            chosen_name = pairs[0][0] or pairs[0][1]

    address = clean_text(chosen_address).strip(" <>\"'(),;").lower()
    display_name = clean_text(chosen_name).strip(" <>\"'")
    if address and not _VALID_ADDRESS.match(address):
        # e.g. "Support Team support@vendor.io": recover the address token from the text.
        recovered = _EMAIL_IN_TEXT.search(raw)
        if recovered:
            address = recovered.group(0).lower()
            display_name = raw.replace(recovered.group(0), "").strip(" <>\"'(),;")
    address_count = sum(1 for _, a in pairs if "@" in a) or (1 if address else 0)

    if not _VALID_ADDRESS.match(address):
        return SenderInfo(display_name, "", "", "", "", False, address_count)

    local_part, _, domain = address.rpartition("@")
    domain = domain.strip(".")
    return SenderInfo(
        display_name=display_name,
        address=address,
        local_part=local_part,
        domain=domain,
        registered_domain=registered_domain(domain),
        is_valid=True,
        address_count=max(address_count, 1),
    )


# ---- Gmail labels ---------------------------------------------------------- #

_LABEL_SPLIT = re.compile(r"[|,;\n]+")
_LABEL_NULLS = frozenset({"", "NONE", "NULL", "NAN", "N/A", "NA", "[]", "{}"})


def normalize_gmail_labels(value: Any) -> frozenset[str]:
    """Normalise inconsistent label encodings into a set of UPPER_SNAKE tokens.

    Accepts ``None``, empty strings, lists/tuples/sets, JSON list strings, and
    ``|``/``,``/``;``/newline delimited strings. Spacing, case, quoting, and
    duplicates are normalised ("Category Promotions" -> ``CATEGORY_PROMOTIONS``).
    """
    if value is None:
        return frozenset()
    items: list[Any]
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        text = _as_text(value).strip()
        if not text:
            return frozenset()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else [text]
            except (ValueError, TypeError):
                items = _LABEL_SPLIT.split(text.strip("[]"))
        else:
            items = _LABEL_SPLIT.split(text)

    labels: set[str] = set()
    for item in items:
        token = clean_text(item).strip(" '\"[](){}")
        token = re.sub(r"[\s\-/]+", "_", token.upper())
        token = re.sub(r"_+", "_", token).strip("_")
        if token not in _LABEL_NULLS:
            labels.add(token)
    return frozenset(labels)


# ---- timestamps ------------------------------------------------------------ #

_MIN_YEAR, _MAX_YEAR = 1995, 2100


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp into an aware UTC ``datetime`` or return ``None``.

    Accepts datetimes, epoch seconds/milliseconds (numbers or digit strings),
    ISO-8601 (including a trailing ``Z``), and RFC 2822 email dates. Naive values
    are assumed to be UTC. Blank, malformed, or implausible values return ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    parsed: datetime | None = None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            if math.isnan(float(value)) or math.isinf(float(value)):
                return None
            number = float(value)
            parsed = datetime.fromtimestamp(
                number / 1000.0 if abs(number) > 1e11 else number, tz=timezone.utc
            )
        else:
            text = clean_text(value)
            if not text:
                return None
            if re.fullmatch(r"-?\d{9,13}(?:\.\d+)?", text):
                return parse_timestamp(float(text))
            iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
            try:
                parsed = datetime.fromisoformat(iso)
            except ValueError:
                parsed = parsedate_to_datetime(text)
    except (ValueError, TypeError, OverflowError, OSError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if not _MIN_YEAR <= parsed.year <= _MAX_YEAR:
        return None
    return parsed


# ---- numbers / booleans ---------------------------------------------------- #

def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return float(value) if isinstance(value, bool) else None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = clean_text(value).lower()
    if not text:
        return None
    if text in {"true", "yes", "y"}:
        return 1.0
    if text in {"false", "no", "n"}:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [clean_text(item) for item in value]
    else:
        text = clean_text(value)
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [clean_text(item) for item in parsed if clean_text(item)]
            except (ValueError, TypeError):
                pass
        items = [part.strip() for part in re.split(r"[|;\n]+", text)]
    return [item for item in items if item]


# --------------------------------------------------------------------------- #
# Attachment metadata (from a Gmail API payload, for future sync use)
# --------------------------------------------------------------------------- #

def attachment_metadata_from_payload(payload: Any) -> dict[str, Any]:
    """Summarise attachments from a Gmail ``users.messages.get`` payload.

    Pure function so the sync layer can store the result later. Malformed or
    missing payloads yield an empty summary instead of raising.
    """
    names: list[str] = []
    mime_types: list[str] = []

    def walk(part: Any, depth: int = 0) -> None:
        if not isinstance(part, Mapping) or depth > 12:
            return
        filename = clean_text(part.get("filename"))
        if filename:
            names.append(filename)
            mime_types.append(clean_text(part.get("mimeType")).lower())
        children = part.get("parts")
        if isinstance(children, list):
            for child in children:
                walk(child, depth + 1)

    walk(payload)
    return {
        "has_attachment": bool(names),
        "attachment_count": len(names),
        "attachment_names": "|".join(names),
        "attachment_mime_types": "|".join(mime_types),
    }


_EXTENSION_GROUPS = {
    "pdf": {"pdf"},
    "calendar": {"ics"},
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic"},
    "document": {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt", "odt", "rtf"},
    "archive": {"zip", "rar", "7z", "tar", "gz"},
}


def _attachment_kinds(names: list[str], mime_types: list[str]) -> set[str]:
    kinds: set[str] = set()
    for name in names:
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        for kind, extensions in _EXTENSION_GROUPS.items():
            if extension in extensions:
                kinds.add(kind)
    for mime in mime_types:
        if "pdf" in mime:
            kinds.add("pdf")
        elif "calendar" in mime:
            kinds.add("calendar")
        elif mime.startswith("image/"):
            kinds.add("image")
        elif any(token in mime for token in ("word", "excel", "sheet", "powerpoint", "presentation", "msword", "csv")):
            kinds.add("document")
        elif any(token in mime for token in ("zip", "rar", "compressed", "tar")):
            kinds.add("archive")
    return kinds


# --------------------------------------------------------------------------- #
# Model text
# --------------------------------------------------------------------------- #

def legacy_model_text(row: Mapping[str, Any]) -> str:
    """The text the original TF-IDF baseline sees (kept byte-for-byte compatible
    with ``scripts.evaluate_priority_engine._features``)."""
    view = model_input_view(row)
    return " ".join(
        (
            view.get("sender") or "",
            view.get("subject") or "",
            view.get("snippet") or "",
            view.get("body_text") or "",
        )
    )


def normalized_model_text(row: Mapping[str, Any]) -> str:
    """Normalised text for TF-IDF: unescaped, NFKC, invisible chars removed,
    body truncated to ``MAX_BODY_CHARS``, plus a sender-domain token."""
    view = model_input_view(row)
    sender = parse_sender(view.get("sender"))
    domain_token = (
        "senderdomain_" + re.sub(r"[^a-z0-9]+", "_", sender.registered_domain)
        if sender.registered_domain
        else ""
    )
    parts = (
        sender.display_name,
        sender.address,
        domain_token,
        clean_text(view.get("subject")),
        clean_text(view.get("snippet")),
        clean_text(view.get("body_text"), max_chars=MAX_BODY_CHARS),
    )
    return " ".join(part for part in parts if part)


# --------------------------------------------------------------------------- #
# Feature extraction
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeatureContext:
    """Everything besides the row that can change feature values.

    ``reference_time`` makes recency deterministic (never ``datetime.now()``). Use
    the same value for training and inference; ``context_for_rows`` derives it from
    message timestamps only, which carry no label information.
    """

    task: str | None = None
    gmail_label_mode: str = "independent"
    reference_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.gmail_label_mode not in GMAIL_LABEL_MODES:
            raise ValueError(
                f"Unknown Gmail label mode {self.gmail_label_mode!r}; "
                f"expected one of {GMAIL_LABEL_MODES}"
            )
        if self.task is not None and self.task not in TASKS:
            raise ValueError(f"Unknown task {self.task!r}; expected one of {TASKS}")
        if self.reference_time is not None and self.reference_time.tzinfo is None:
            raise ValueError("reference_time must be timezone-aware")


def context_for_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    task: str | None = None,
    gmail_label_mode: str = "independent",
) -> FeatureContext:
    """Build a context whose reference time is the latest message timestamp."""
    latest: datetime | None = None
    for row in rows:
        stamp = _row_timestamp(model_input_view(row))
        if stamp is not None and (latest is None or stamp > latest):
            latest = stamp
    return FeatureContext(task=task, gmail_label_mode=gmail_label_mode, reference_time=latest)


def _row_timestamp(view: Mapping[str, Any]) -> datetime | None:
    for key in ("message_date", "date", "received_at"):
        stamp = parse_timestamp(view.get(key))
        if stamp is not None:
            return stamp
    return None


def _raw_has_timestamp_value(view: Mapping[str, Any]) -> bool:
    return any(clean_text(view.get(key)) for key in ("message_date", "date", "received_at"))


_REPLY_PREFIX = re.compile(r"^\s*((?:re|aw|sv)\s*(?:\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_FORWARD_PREFIX = re.compile(r"^\s*(?:fwd?|tr)\s*:", re.IGNORECASE)
_QUOTED_REPLY = re.compile(
    r"(?:^|\s)on .{5,120}? wrote:|-{2,}\s*original message\s*-{2,}|"
    r"(?:^|\s)from:\s.{0,200}?\ssent:\s|(?:^|\s)>\s?\w",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

_URGENCY = re.compile(
    r"\b(urgent(?:ly)?|asap|immediate(?:ly)?|action required|important|time[- ]sensitive|"
    r"final notice|last chance|critical|attention required)\b", re.IGNORECASE)
_DEADLINE = re.compile(
    r"\b(deadline|due (?:date|on|by|today|tomorrow)|by (?:eod|end of day|tomorrow|today|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)|expires?(?: on| in| today)?|within \d+ (?:hours?|days?)|"
    r"before \d{1,2}(?::\d{2})? ?(?:am|pm)|last date|closing date)\b", re.IGNORECASE)
_FINANCE = re.compile(
    r"\b(invoice|payment|paid|bill(?:ing)?|statement|transaction|credit(?:ed)?|debit(?:ed)?|"
    r"account balance|emi|loan|salary|refund|tax|upi|bank|receipt|order total|rs\.?|inr|usd)\b|[$₹€£]\s?\d",
    re.IGNORECASE)
_JOB = re.compile(
    r"\b(interview|application|apply|applied|hiring|recruiter|assessment|offer letter|job|career|"
    r"position|vacancy|resume|candidate|shortlisted|internship|opening)\b", re.IGNORECASE)
_PROMO = re.compile(
    r"\b(sale|discount|% off|\d+% off|coupon|promo(?:tion)?|deal|limited time|exclusive|free shipping|"
    r"shop now|buy now|subscribe|newsletter|unsubscribe|webinar|offer ends)\b", re.IGNORECASE)
_REQUEST = re.compile(
    r"\b(please|could you|can you|would you|kindly|let me know|get back to me|reply|respond|confirm|"
    r"your thoughts|waiting for|follow(?:ing)? up|need your)\b", re.IGNORECASE)
_SECURITY = re.compile(
    r"\b(otp|one[- ]time password|verification code|security alert|password|sign[- ]in|login|"
    r"verify your|2fa|two[- ]factor)\b", re.IGNORECASE)
_PERSONAL_GREETING = re.compile(r"^\s*(?:hi|hello|hey|dear)\s+[a-z][a-z .'-]{0,30}[,!:]", re.IGNORECASE)
_UNSUBSCRIBE = re.compile(r"\bunsubscribe\b|\bmanage (?:your )?(?:email )?preferences\b|\bopt[- ]out\b", re.IGNORECASE)


def _log1p(value: float) -> float:
    return math.log1p(max(value, 0.0))


def _capped(count: int, cap: int) -> float:
    return float(min(count, cap))


def _cyclical(value: float, period: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _compute(view: Mapping[str, Any], context: FeatureContext) -> dict[str, float]:
    """Compute every feature in a fixed order. Pure function of (view, context)."""
    features: dict[str, float] = {}

    # ---- sender --------------------------------------------------------- #
    sender_raw = clean_text(view.get("sender"))
    sender = parse_sender(view.get("sender"))
    local = sender.local_part
    first_label = sender.domain.split(".")[0] if sender.domain else ""
    features["sender_missing"] = float(not sender_raw)
    features["sender_invalid"] = float(bool(sender_raw) and not sender.is_valid)
    features["sender_has_display_name"] = float(bool(sender.display_name))
    features["sender_is_noreply"] = float(bool(local) and bool(_NOREPLY_LOCAL.search(local)))
    features["sender_bulk_local"] = float(bool(local) and bool(_BULK_LOCAL.match(local)))
    features["sender_bulk_domain"] = float(
        sender.is_valid
        and (first_label in _BULK_DOMAIN_FIRST_LABELS or sender.registered_domain in _ESP_REGISTERED_DOMAINS)
    )
    features["sender_is_freemail"] = float(sender.domain in FREEMAIL_DOMAINS)
    features["sender_domain_label_count"] = _capped(len(sender.domain.split(".")) if sender.domain else 0, 6)
    features["sender_local_digit_ratio"] = (
        sum(ch.isdigit() for ch in local) / len(local) if local else 0.0
    )
    features["sender_address_count"] = _capped(sender.address_count, 5)

    # ---- subject -------------------------------------------------------- #
    subject = clean_text(view.get("subject"))
    reply_match = _REPLY_PREFIX.match(subject)
    reply_depth = len(re.findall(r"(?:re|aw|sv)\s*(?:\[\d+\])?\s*:", reply_match.group(0), re.IGNORECASE)) if reply_match else 0
    letters = [ch for ch in subject if ch.isalpha()]
    features["subject_missing"] = float(not subject)
    features["subject_len_log"] = _log1p(len(subject))
    features["subject_reply_depth"] = _capped(reply_depth, 5)
    features["subject_is_forward"] = float(bool(_FORWARD_PREFIX.match(subject)))
    features["subject_has_question"] = float("?" in subject)
    features["subject_has_exclaim"] = float("!" in subject)
    features["subject_upper_ratio"] = (
        sum(ch.isupper() for ch in letters) / len(letters) if len(letters) >= 4 else 0.0
    )
    features["subject_has_digits"] = float(any(ch.isdigit() for ch in subject))

    # ---- snippet / body ------------------------------------------------- #
    snippet = clean_text(view.get("snippet"))
    body_raw = clean_text(view.get("body_text"))
    body = body_raw[:MAX_BODY_CHARS]
    scan_text = " ".join(
        part for part in (subject, snippet, body[:KEYWORD_SCAN_BODY_CHARS]) if part
    )
    features["snippet_missing"] = float(not snippet)
    features["body_missing"] = float(not body_raw)
    features["body_len_log"] = _log1p(len(body_raw))
    features["body_truncated"] = float(len(body_raw) > MAX_BODY_CHARS)
    features["body_has_quoted_reply"] = float(bool(_QUOTED_REPLY.search(body)))
    features["body_question_count"] = _capped(body.count("?"), 10)
    features["body_url_count"] = _capped(len(_URL.findall(body)), 20)
    features["body_has_unsubscribe"] = float(bool(_UNSUBSCRIBE.search(body)))
    features["body_digit_ratio"] = (
        sum(ch.isdigit() for ch in body) / len(body) if body else 0.0
    )
    features["body_personal_greeting"] = float(bool(_PERSONAL_GREETING.match(body or snippet)))
    features["hits_urgency"] = _capped(len(_URGENCY.findall(scan_text)), 5)
    features["hits_deadline"] = _capped(len(_DEADLINE.findall(scan_text)), 5)
    features["hits_finance"] = _capped(len(_FINANCE.findall(scan_text)), 8)
    features["hits_job"] = _capped(len(_JOB.findall(scan_text)), 8)
    features["hits_promo"] = _capped(len(_PROMO.findall(scan_text)), 8)
    features["hits_request"] = _capped(len(_REQUEST.findall(scan_text)), 6)
    features["hits_security"] = _capped(len(_SECURITY.findall(scan_text)), 5)

    # ---- recipient ------------------------------------------------------ #
    recipient_raw = clean_text(view.get("recipient"))
    recipient_count = 0
    if recipient_raw:
        recipient_count = sum(1 for _, address in getaddresses([recipient_raw]) if "@" in address)
    features["recipient_missing"] = float(not recipient_raw)
    features["recipient_count"] = _capped(recipient_count, 10)
    features["recipient_is_bulk"] = float(recipient_count >= 5)

    # ---- timestamp / recency ------------------------------------------- #
    stamp = _row_timestamp(view)
    reference = context.reference_time
    features["ts_missing"] = float(not _raw_has_timestamp_value(view))
    features["ts_invalid"] = float(_raw_has_timestamp_value(view) and stamp is None)
    if stamp is not None:
        hour_sin, hour_cos = _cyclical(stamp.hour + stamp.minute / 60.0, 24.0)
        dow_sin, dow_cos = _cyclical(float(stamp.weekday()), 7.0)
        features["ts_hour_sin"], features["ts_hour_cos"] = hour_sin, hour_cos
        features["ts_dow_sin"], features["ts_dow_cos"] = dow_sin, dow_cos
        features["ts_is_weekend"] = float(stamp.weekday() >= 5)
        age_days = (reference - stamp).total_seconds() / 86400.0 if reference is not None else 0.0
        features["ts_age_days_log"] = _log1p(age_days)
    else:
        for name in ("ts_hour_sin", "ts_hour_cos", "ts_dow_sin", "ts_dow_cos", "ts_is_weekend", "ts_age_days_log"):
            features[name] = 0.0

    # ---- thread / reply metadata --------------------------------------- #
    thread_count = _to_float(view.get("thread_message_count"))
    in_reply_to = clean_text(view.get("in_reply_to"))
    raw_references = view.get("references")
    if isinstance(raw_references, (list, tuple, set, frozenset)):
        references = [clean_text(item) for item in raw_references if clean_text(item)]
    else:
        references = [part for part in re.split(r"[\s|,;]+", clean_text(raw_references)) if part]
    features["thread_info_available"] = float(
        bool(clean_text(view.get("thread_id"))) or thread_count is not None or bool(in_reply_to) or bool(references)
    )
    features["thread_message_count_log"] = _log1p(thread_count) if thread_count is not None else 0.0
    features["thread_has_replies"] = float(thread_count is not None and thread_count > 1)
    features["in_reply_to_present"] = float(bool(in_reply_to))
    features["references_count"] = _capped(len(references), 10)
    features["is_reply"] = float(reply_depth > 0 or bool(in_reply_to))

    # ---- attachments ---------------------------------------------------- #
    names = _split_list(view.get("attachment_names"))
    mimes = [item.lower() for item in _split_list(view.get("attachment_mime_types"))]
    count_value = _to_float(view.get("attachment_count"))
    flag_value = _to_float(view.get("has_attachment"))
    available = any(
        value is not None for value in (count_value, flag_value)
    ) or bool(names) or bool(mimes)
    attachment_count = (
        count_value if count_value is not None
        else float(len(names)) if names
        else (1.0 if flag_value else 0.0)
    )
    kinds = _attachment_kinds(names, mimes)
    features["attachment_info_available"] = float(available)
    features["has_attachment"] = float(attachment_count > 0)
    features["attachment_count_log"] = _log1p(attachment_count)
    for kind in ("pdf", "calendar", "image", "document", "archive"):
        features[f"attachment_has_{kind}"] = float(kind in kinds)

    # ---- Gmail labels (policy-controlled) ------------------------------ #
    normalized_labels = normalize_gmail_labels(view.get("labels"))
    allowed_labels = safe_gmail_labels(normalized_labels, context.task, context.gmail_label_mode)
    features["gmail_labels_available"] = float(bool(allowed_labels))
    for label in LABEL_FEATURE_ORDER:
        features[f"gmail_label_{label}"] = float(label in allowed_labels)

    return features


# Canonical feature order, derived once from the extractor itself so the list can
# never drift from the implementation.
FEATURE_NAMES: tuple[str, ...] = tuple(_compute({}, FeatureContext()).keys())


def extract_features(
    row: Mapping[str, Any],
    context: FeatureContext | None = None,
) -> dict[str, float]:
    """Return ``{feature_name: float}`` for one row, in canonical order.

    Reads only ``ALLOWED_INPUT_COLUMNS`` via ``model_input_view`` and never raises
    for missing or malformed metadata.
    """
    features = _compute(model_input_view(row), context or FeatureContext())
    # Defensive: guarantee finite floats and the canonical key order.
    return {
        name: (float(features[name]) if math.isfinite(float(features[name])) else 0.0)
        for name in FEATURE_NAMES
    }


def feature_vector(
    row: Mapping[str, Any],
    context: FeatureContext | None = None,
) -> list[float]:
    """Return the feature values of ``row`` as a list in ``FEATURE_NAMES`` order."""
    values = extract_features(row, context)
    return [values[name] for name in FEATURE_NAMES]


def feature_matrix(
    rows: Iterable[Mapping[str, Any]],
    context: FeatureContext | None = None,
) -> list[list[float]]:
    """Vectorise many rows with one shared context."""
    return [feature_vector(row, context) for row in rows]


def feature_schema() -> dict[str, Any]:
    """Describe the feature definitions (stored next to any trained model)."""
    return {
        "version": FEATURE_SCHEMA_VERSION,
        "names": list(FEATURE_NAMES),
        "max_body_chars": MAX_BODY_CHARS,
        "allowed_input_columns": list(ALLOWED_INPUT_COLUMNS),
    }


def schema_fingerprint() -> str:
    """Short hash of the feature definitions; changes whenever the schema does."""
    payload = json.dumps(feature_schema(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
