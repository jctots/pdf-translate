"""
Unit tests for paperless_webhook/webhook.py.

Covers:
  - _bool_env() helper
  - /health and /webhook FastAPI endpoints (doc_id extraction paths)
  - detect_language()
  - set_failure_tag() — apply, remove, no-op
  - handle() idempotency guards (auto-translated companion, already-translated original)
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import webhook with test env vars set before module-level reads
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "paperless_webhook"))

os.environ.setdefault("PAPERLESS_API_TOKEN", "test-token")
os.environ.setdefault("PAPERLESS_URL",        "http://paperless:8000")
os.environ.setdefault("LIBRETRANSLATE_URL",   "http://lt:5000")
os.environ.setdefault("PDF_TRANSLATE_URL",    "http://pdft:7860")

import webhook  # noqa: E402  (must come after sys.path + env setup)
from webhook import (  # noqa: E402
    FIELD_TRANSLATION,
    TAG_AUTO_TRANSLATED,
    TAG_TRANSLATION_FAILED,
    _bool_env,
    app,
    detect_language,
    set_failure_tag,
)

from fastapi.testclient import TestClient  # noqa: E402

http_client = TestClient(app)


# ---------------------------------------------------------------------------
# _bool_env
# ---------------------------------------------------------------------------

class TestBoolEnv:
    def test_missing_returns_default_false(self, monkeypatch):
        monkeypatch.delenv("_TEST_BOOL", raising=False)
        assert _bool_env("_TEST_BOOL", False) is False

    def test_missing_returns_default_true(self, monkeypatch):
        monkeypatch.delenv("_TEST_BOOL", raising=False)
        assert _bool_env("_TEST_BOOL", True) is True

    def test_true_string(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "true")
        assert _bool_env("_TEST_BOOL", False) is True

    def test_one_string(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "1")
        assert _bool_env("_TEST_BOOL", False) is True

    def test_yes_string(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "yes")
        assert _bool_env("_TEST_BOOL", False) is True

    def test_false_string(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "false")
        assert _bool_env("_TEST_BOOL", True) is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "TRUE")
        assert _bool_env("_TEST_BOOL", False) is True

    def test_empty_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_BOOL", "")
        assert _bool_env("_TEST_BOOL", True) is True


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_ok(self):
        r = http_client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /webhook — doc_id extraction
# ---------------------------------------------------------------------------

class TestWebhookDocIdExtraction:
    """The endpoint responds 200 immediately and queues a background task.
    We test the doc_id extraction logic; handle() itself is tested separately."""

    def test_no_doc_id_returns_ignored(self):
        r = http_client.post("/webhook", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    def test_doc_id_from_body_id_field(self):
        r = http_client.post("/webhook", json={"id": 42})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "accepted"
        assert int(data["document_id"]) == 42

    def test_doc_id_from_body_document_id_field(self):
        r = http_client.post("/webhook", json={"document_id": 99})
        assert r.status_code == 200
        assert int(r.json()["document_id"]) == 99

    def test_doc_id_from_doc_url_in_body(self):
        r = http_client.post("/webhook", json={
            "doc_url": "http://paperless:8000/documents/123/"
        })
        assert r.status_code == 200
        assert r.json()["document_id"] == "123"

    def test_doc_id_from_query_param(self):
        r = http_client.post("/webhook?id=77")
        assert r.status_code == 200
        assert r.json()["document_id"] == "77"

    def test_doc_id_from_doc_url_query_param(self):
        r = http_client.post(
            "/webhook?doc_url=http://paperless:8000/documents/55/"
        )
        assert r.status_code == 200
        assert r.json()["document_id"] == "55"

    def test_malformed_body_returns_ignored(self):
        r = http_client.post(
            "/webhook",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_empty_text_returns_none(self):
        assert detect_language("") is None
        assert detect_language(None) is None

    def test_successful_detection(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [{"language": "de", "confidence": 0.99}]
        with patch("httpx.post", return_value=mock_resp):
            assert detect_language("Guten Tag") == "de"

    def test_empty_results_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        with patch("httpx.post", return_value=mock_resp):
            assert detect_language("hello") is None

    def test_request_failure_returns_none(self):
        import httpx as _httpx
        with patch("httpx.post", side_effect=_httpx.ConnectError("refused")):
            assert detect_language("hello") is None


# ---------------------------------------------------------------------------
# set_failure_tag
# ---------------------------------------------------------------------------

class TestSetFailureTag:
    def _mock_client(self):
        client = MagicMock()
        # get_or_create_tag fetches tags via client.get — stub it
        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {"results": [{"id": 99, "name": TAG_TRANSLATION_FAILED}]}
        client.get.return_value = get_resp
        # patch call returns success
        patch_resp = MagicMock()
        patch_resp.raise_for_status = MagicMock()
        client.patch.return_value = patch_resp
        return client

    def test_applies_tag_when_not_present(self):
        client = self._mock_client()
        doc = {"tags": [1, 2]}
        set_failure_tag(client, 42, doc, failed=True)
        client.patch.assert_called_once()
        call_kwargs = client.patch.call_args
        assert 99 in call_kwargs.kwargs["json"]["tags"]

    def test_removes_tag_when_present(self):
        client = self._mock_client()
        doc = {"tags": [1, 99, 2]}
        set_failure_tag(client, 42, doc, failed=False)
        client.patch.assert_called_once()
        call_kwargs = client.patch.call_args
        assert 99 not in call_kwargs.kwargs["json"]["tags"]

    def test_no_patch_when_tag_already_absent_on_success(self):
        client = self._mock_client()
        doc = {"tags": [1, 2]}  # tag 99 not present
        set_failure_tag(client, 42, doc, failed=False)
        client.patch.assert_not_called()

    def test_no_patch_when_tag_already_present_on_failure(self):
        client = self._mock_client()
        doc = {"tags": [1, 99, 2]}  # tag already there
        set_failure_tag(client, 42, doc, failed=True)
        client.patch.assert_not_called()

    def test_silently_handles_api_error(self):
        client = MagicMock()
        client.get.side_effect = Exception("network error")
        # Should not raise
        set_failure_tag(client, 42, {"tags": []}, failed=True)


# ---------------------------------------------------------------------------
# handle() — idempotency guards
# ---------------------------------------------------------------------------

def _make_doc(doc_id: int, tags: list[int] | None = None, custom_fields: list | None = None) -> dict:
    return {
        "id": doc_id,
        "title": "Test Document",
        "content": "Guten Tag",
        "tags": tags or [],
        "custom_fields": custom_fields or [],
    }


class TestHandleIdempotency:
    """Test that handle() skips documents that should not be translated."""

    def _run_handle(self, doc: dict) -> list[dict]:
        """Run handle() with a mocked Paperless that returns the given doc.
        Returns all emit() calls captured via the log."""
        emitted = []

        original_emit = webhook.emit
        def capture_emit(entry):
            emitted.append(entry)
        webhook.emit = capture_emit

        try:
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client

                # get_document → return our doc
                get_resp = MagicMock()
                get_resp.raise_for_status = MagicMock()
                get_resp.json.return_value = doc
                mock_client.get.return_value = get_resp

                # post for create tag → not needed for skip paths

                webhook.handle(doc["id"], doc.get("content"))
        finally:
            webhook.emit = original_emit

        return emitted

    def test_skips_auto_translated_companion(self):
        """Document tagged 'auto-translated' must be skipped immediately."""
        # First, make get_or_create_tag return the auto-translated tag id
        # by having the GET /api/tags/?name=auto-translated return id=50
        auto_tag_id = 50
        doc = _make_doc(doc_id=201, tags=[auto_tag_id])

        emitted = []
        original_emit = webhook.emit
        webhook.emit = lambda e: emitted.append(e)

        try:
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client

                # get_document
                doc_resp = MagicMock()
                doc_resp.raise_for_status = MagicMock()
                doc_resp.json.return_value = doc
                # tags lookup (get_tag_id_by_name)
                tag_resp = MagicMock()
                tag_resp.raise_for_status = MagicMock()
                tag_resp.json.return_value = {"results": [{"id": auto_tag_id, "name": TAG_AUTO_TRANSLATED}]}

                mock_client.get.side_effect = [doc_resp, tag_resp]

                webhook.handle(201, "Guten Tag")
        finally:
            webhook.emit = original_emit

        assert len(emitted) == 1
        assert emitted[0]["action"] == "skipped"
        assert emitted[0]["reason"] == "auto-translated companion"

    def test_field_lookup_failure_applies_failure_tag(self):
        """handle() applies translation-failed tag when custom field lookup fails (step 4)."""
        doc = _make_doc(doc_id=303)

        emitted = []
        original_emit = webhook.emit
        webhook.emit = lambda e: emitted.append(e)

        try:
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client

                # get_document
                doc_resp = MagicMock()
                doc_resp.raise_for_status = MagicMock()
                doc_resp.json.return_value = doc
                # get_tag_id_by_name (auto-translated) → tag not present
                tag_resp = MagicMock()
                tag_resp.raise_for_status = MagicMock()
                tag_resp.json.return_value = {"results": [{"id": 50, "name": TAG_AUTO_TRANSLATED}]}
                # get_custom_field_ids → raises
                mock_client.get.side_effect = [doc_resp, tag_resp, Exception("Paperless API error")]

                # set_failure_tag calls get_or_create_tag (POST /api/tags) and then PATCH
                failure_tag_resp = MagicMock()
                failure_tag_resp.raise_for_status = MagicMock()
                failure_tag_resp.json.return_value = {"results": [{"id": 99, "name": TAG_TRANSLATION_FAILED}]}
                mock_client.get.side_effect = [doc_resp, tag_resp, Exception("Paperless API error"),
                                               failure_tag_resp]
                patch_resp = MagicMock()
                patch_resp.raise_for_status = MagicMock()
                mock_client.patch.return_value = patch_resp

                with patch.object(webhook, "SOURCE_LANG", "auto"):
                    webhook.handle(303, "Guten Tag")
        finally:
            webhook.emit = original_emit

        assert any(e["action"] == "error" and "field lookup failed" in e["reason"] for e in emitted)
        mock_client.patch.assert_called()

    def test_skips_already_translated_original(self):
        """Original document with 'translation' field set must be skipped."""
        translation_field_id = 10
        doc = _make_doc(
            doc_id=142,
            custom_fields=[{"field": translation_field_id, "value": [201]}],
        )

        emitted = []
        original_emit = webhook.emit
        webhook.emit = lambda e: emitted.append(e)

        try:
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client

                # get_document
                doc_resp = MagicMock()
                doc_resp.raise_for_status = MagicMock()
                doc_resp.json.return_value = doc
                # get_tag_id_by_name (auto-translated) → tag not present on this doc
                tag_resp = MagicMock()
                tag_resp.raise_for_status = MagicMock()
                tag_resp.json.return_value = {"results": [{"id": 50, "name": TAG_AUTO_TRANSLATED}]}
                # detect_language → returns target lang (en) so language check passes
                # get_custom_field_ids
                fields_resp = MagicMock()
                fields_resp.raise_for_status = MagicMock()
                fields_resp.json.return_value = {
                    "results": [
                        {"id": translation_field_id, "name": "translation"},
                    ]
                }

                mock_client.get.side_effect = [doc_resp, tag_resp, fields_resp]

                with patch.object(webhook, "SOURCE_LANG", "auto"):
                    webhook.handle(142, "Guten Tag")
        finally:
            webhook.emit = original_emit

        assert len(emitted) == 1
        assert emitted[0]["action"] == "skipped"
        assert emitted[0]["reason"] == "already translated"


# ---------------------------------------------------------------------------
# Webhook API key authentication
# ---------------------------------------------------------------------------

class TestWebhookAuth:
    """WEBHOOK_API_KEY env var gates POST /webhook; GET /health stays open."""

    def test_no_key_configured_allows_webhook(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)
        r = http_client.post("/webhook", json={"id": 1})
        # accepted or ignored — either is fine; must not be 401
        assert r.status_code == 200
        assert r.json().get("status") != "unauthorized"

    def test_valid_key_allows_webhook(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_API_KEY", "whsec123")
        r = http_client.post("/webhook", json={"id": 1},
                             headers={"Authorization": "Bearer whsec123"})
        assert r.status_code == 200

    def test_wrong_key_returns_401(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_API_KEY", "whsec123")
        r = http_client.post("/webhook", json={"id": 1},
                             headers={"Authorization": "Bearer wrongkey"})
        assert r.status_code == 401

    def test_missing_auth_header_returns_401(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_API_KEY", "whsec123")
        r = http_client.post("/webhook", json={"id": 1})
        assert r.status_code == 401

    def test_health_always_open(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_API_KEY", "whsec123")
        r = http_client.get("/health")
        assert r.status_code == 200
