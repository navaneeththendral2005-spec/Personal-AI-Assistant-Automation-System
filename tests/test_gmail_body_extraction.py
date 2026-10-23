import base64

from backend.services.gmail_service import (
    _clean_plain_text,
    _extract_plain_text,
    _looks_like_html,
    _needs_body_repair,
)


def encode_body(text: str) -> str:
    """Encode text using Gmail's base64url body format."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_extract_plain_text_from_simple_message():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": encode_body("Hello from FRIDAY!")},
    }
    assert _extract_plain_text(payload) == "Hello from FRIDAY!"


def test_extract_plain_text_prefers_plain_text_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": encode_body("<p>Hello HTML</p>")}},
            {"mimeType": "text/plain", "body": {"data": encode_body("Hello plain")}},
        ],
    }
    assert _extract_plain_text(payload) == "Hello plain"


def test_extract_plain_text_from_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/related",
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {"mimeType": "text/plain", "body": {"data": encode_body("Nested message")}}
                        ],
                    }
                ],
            }
        ],
    }
    assert _extract_plain_text(payload) == "Nested message"


def test_mislabelled_html_inside_text_plain_is_converted():
    html = (
        '<!DOCTYPE html><html><head><title>Newsletter</title></head>'
        '<body><h1>Career opportunities</h1><p>Find a role that fits you.</p>'
        '<script>tracking()</script></body></html>'
    )
    payload = {"mimeType": "text/plain", "body": {"data": encode_body(html)}}

    result = _extract_plain_text(payload)

    assert "Career opportunities" in result
    assert "Find a role that fits you." in result
    assert "<!DOCTYPE" not in result
    assert "<html" not in result
    assert "tracking()" not in result


def test_html_only_payload_is_converted_to_readable_text():
    payload = {
        "mimeType": "text/html",
        "body": {"data": encode_body("<html><body><p>Hello</p><p>from FRIDAY</p></body></html>")},
    }
    result = _extract_plain_text(payload)
    assert "Hello" in result
    assert "from FRIDAY" in result
    assert "<p>" not in result


def test_html_fragment_mislabelled_as_plain_text_is_converted():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": encode_body("<div><p>First paragraph</p><p>Second paragraph</p></div>")},
    }
    result = _extract_plain_text(payload)
    assert "First paragraph" in result
    assert "Second paragraph" in result
    assert "<div>" not in result


def test_body_can_be_loaded_from_gmail_attachment_payload():
    payload = {
        "mimeType": "text/plain",
        "body": {"attachmentId": "part-1", "size": 18},
    }
    result = _extract_plain_text(payload, attachment_loader=lambda attachment_id: encode_body("Loaded from part"))
    assert result == "Loaded from part"


def test_unicode_layout_whitespace_is_normalized():
    assert _clean_plain_text("\ufeffHello\u2007\u2007world\u200b\n\n\nNext") == "Hello world\n\nNext"


def test_html_detection_does_not_treat_ordinary_prose_as_html():
    assert _looks_like_html("This email says <3 and asks me to reply.") is False
    assert _looks_like_html("<!DOCTYPE html><html><body>Hello</body></html>") is True


def test_repair_detection_includes_nonstandard_unicode_whitespace():
    assert _needs_body_repair("Hello\u2007world") is True
    assert _needs_body_repair("\ufeffHello") is True
    assert _needs_body_repair("Hello world") is False


def test_extract_plain_text_returns_empty_for_missing_body():
    assert _extract_plain_text({"mimeType": "text/plain", "body": {}}) == ""
    assert _extract_plain_text({}) == ""


def test_html_conversion_preserves_paragraphs_and_list_items():
    payload = {
        "mimeType": "text/html",
        "body": {
            "data": encode_body(
                "<html><body><div>Hello, <b>Navaneeth</b>.</div>"
                "<p>Paragraph one.</p><p>Paragraph two.</p>"
                "<ul><li>First</li><li>Second</li></ul></body></html>"
            )
        },
    }
    result = _extract_plain_text(payload)
    assert "Hello, Navaneeth." in result
    assert "Paragraph one." in result
    assert "Paragraph two." in result
    assert "- First" in result
    assert "- Second" in result
    assert "\n" in result
