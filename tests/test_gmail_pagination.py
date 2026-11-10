"""Unit tests for read-only Gmail API pagination using a fake service."""
from backend.services.gmail_service import list_emails, list_emails_page


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeMessages:
    def __init__(self):
        self.list_calls = []
        self.get_calls = []
        self.pages = {
            None: {"messages": [{"id": "msg-1", "threadId": "thread-1"}], "nextPageToken": "next-1"},
            "next-1": {"messages": [{"id": "msg-2", "threadId": "thread-2"}]},
        }
        self.details = {
            "msg-1": {
                "id": "msg-1",
                "threadId": "thread-1",
                "snippet": "Preview one",
                "labelIds": ["INBOX"],
                "payload": {"headers": [
                    {"name": "From", "value": "one@example.test"},
                    {"name": "Subject", "value": "First synthetic email"},
                    {"name": "Date", "value": "Tue, 20 Oct 2026 10:54:57 +0000"},
                ], "mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
            },
            "msg-2": {
                "id": "msg-2",
                "threadId": "thread-2",
                "snippet": "Preview two",
                "labelIds": ["INBOX"],
                "payload": {"headers": [
                    {"name": "From", "value": "two@example.test"},
                    {"name": "Subject", "value": "Second synthetic email"},
                    {"name": "Date", "value": "Tue, 20 Oct 2026 10:54:57 +0000"},
                ], "mimeType": "text/plain", "body": {"data": "SGVsbG8gdHdv"}},
            },
        }

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        token = kwargs.get("pageToken")
        return FakeRequest(self.pages[token])

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.details[kwargs["id"]])


class FakeThreads:
    def __init__(self, details):
        self.details = details
        self.get_calls = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.details[kwargs["id"]])


class FakeUsers:
    def __init__(self):
        self._messages = FakeMessages()
        self._threads = FakeThreads({
            "thread-1": {
                "messages": [
                    self._messages.details["msg-1"],
                    {
                        "id": "msg-old",
                        "threadId": "thread-1",
                        "snippet": "Older conversation message",
                        "labelIds": ["SENT"],
                        "payload": {"mimeType": "text/plain", "body": {"data": "SGVudCBhIHJlcGx5"}},
                    },
                ]
            }
        })

    def messages(self):
        return self._messages

    def threads(self):
        return self._threads


class FakeGmailService:
    def __init__(self):
        self._users = FakeUsers()

    def users(self):
        return self._users


def test_list_emails_page_returns_next_token_and_uses_read_only_calls():
    service = FakeGmailService()

    first = list_emails_page(max_results=50, service=service)
    second = list_emails_page(
        max_results=50,
        page_token=first["next_page_token"],
        service=service,
    )

    assert [email["id"] for email in first["emails"]] == ["msg-1"]
    assert first["next_page_token"] == "next-1"
    assert [email["id"] for email in second["emails"]] == ["msg-2"]
    assert second["next_page_token"] is None
    assert service._users._messages.list_calls[1]["pageToken"] == "next-1"
    assert all(call["maxResults"] <= 50 for call in service._users._messages.list_calls)
    assert all(call["format"] == "full" for call in service._users._messages.get_calls)


def test_list_emails_keeps_list_return_shape_and_accepts_page_token():
    service = FakeGmailService()

    emails = list_emails(max_results=10, page_token="next-1", service=service)

    assert isinstance(emails, list)
    assert [email["id"] for email in emails] == ["msg-2"]



def test_list_emails_page_can_expand_full_thread_history_read_only():
    service = FakeGmailService()
    result = list_emails_page(
        max_results=50, service=service, include_thread_history=True
    )

    assert {email["id"] for email in result["emails"]} == {"msg-1", "msg-old"}
    assert result["listed_count"] == 1
    assert result["expanded_thread_history"] is True
    assert service._users._threads.get_calls == [
        {"userId": "me", "id": "thread-1", "format": "full"}
    ]
    assert service._users._messages.get_calls == []
