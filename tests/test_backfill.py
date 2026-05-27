"""
Unit tests for paperless_webhook/backfill.py.

Covers:
  - _check() eligibility logic (all guard paths + happy path)
  - run() dry-run stops before translating
  - run() emit capture maps translated/skipped/error correctly
  - run() counts (translated, skipped, not_found, error)
"""

import os
import sys
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Shared test setup — env vars must be set before importing webhook/backfill
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "paperless_webhook"))

os.environ.setdefault("PAPERLESS_API_TOKEN", "test-token")
os.environ.setdefault("PAPERLESS_URL",        "http://paperless:8000")
os.environ.setdefault("LIBRETRANSLATE_URL",   "http://lt:5000")
os.environ.setdefault("PDF_TRANSLATE_URL",    "http://pdft:7860")

import backfill  # noqa: E402
import webhook   # noqa: E402
from backfill import _check, run  # noqa: E402
from webhook import FIELD_TRANSLATION, TAG_AUTO_TRANSLATED  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(
    doc_id: int = 1,
    title: str = "Test Doc",
    tags: list[int] | None = None,
    custom_fields: list | None = None,
    content: str = "Guten Tag",
) -> dict:
    return {
        "id": doc_id,
        "title": title,
        "content": content,
        "tags": tags or [],
        "custom_fields": custom_fields or [],
    }


def _mock_client(doc: dict | None = None, status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Client whose .get() returns the given doc."""
    client = MagicMock()
    resp = MagicMock()
    if status_code == 404:
        resp.status_code = 404
        http_exc = httpx.HTTPStatusError("404", request=MagicMock(), response=resp)
        client.get.side_effect = http_exc
    elif status_code != 200:
        resp.status_code = status_code
        http_exc = httpx.HTTPStatusError(str(status_code), request=MagicMock(), response=resp)
        client.get.side_effect = http_exc
    else:
        resp.raise_for_status = MagicMock()
        resp.json.return_value = doc
        client.get.return_value = resp
    return client


# ---------------------------------------------------------------------------
# _check() — eligibility guard paths
# ---------------------------------------------------------------------------

class TestCheck:
    AUTO_TAG_ID = 50
    TRANS_FIELD_ID = 10

    def test_not_found_on_404(self):
        client = _mock_client(status_code=404)
        status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "not_found"

    def test_error_on_unexpected_http_error(self):
        client = _mock_client(status_code=500)
        status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "error"
        assert "500" in detail

    def test_error_on_connection_error(self):
        client = MagicMock()
        client.get.side_effect = Exception("connection refused")
        status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "error"
        assert "connection refused" in detail

    def test_skip_auto_translated_companion(self):
        doc = _make_doc(tags=[self.AUTO_TAG_ID])
        client = _mock_client(doc)
        status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "skip"
        assert detail == "auto-translated companion"

    def test_skip_already_translated(self):
        doc = _make_doc(custom_fields=[{"field": self.TRANS_FIELD_ID, "value": [99]}])
        client = _mock_client(doc)
        status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "skip"
        assert detail == "already translated"

    def test_skip_wrong_language(self):
        doc = _make_doc(content="Hello world")
        client = _mock_client(doc)
        with patch.object(webhook, "SOURCE_LANG", "de"):
            with patch("backfill.SOURCE_LANG", "de"):
                with patch("backfill.detect_language", return_value="en"):
                    status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "skip"
        assert "lang=en" in detail
        assert "want de" in detail

    def test_translate_when_eligible(self):
        doc = _make_doc(title="Mietvertrag 2024", content="Guten Tag")
        client = _mock_client(doc)
        with patch("backfill.SOURCE_LANG", "auto"):
            status, detail = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        assert status == "translate"
        assert detail == "Mietvertrag 2024"

    def test_translate_when_no_tag_id_cached(self):
        """auto_tag_id=None means the tag doesn't exist yet — should not skip."""
        doc = _make_doc(tags=[50])  # has some tag but auto_tag_id is unknown
        client = _mock_client(doc)
        with patch("backfill.SOURCE_LANG", "auto"):
            status, _ = _check(client, 1, None, self.TRANS_FIELD_ID)
        assert status == "translate"

    def test_translate_when_no_field_id_cached(self):
        """translation_field_id=None means the field doesn't exist yet — should not skip."""
        doc = _make_doc(custom_fields=[{"field": 10, "value": [99]}])
        client = _mock_client(doc)
        with patch("backfill.SOURCE_LANG", "auto"):
            status, _ = _check(client, 1, self.AUTO_TAG_ID, None)
        assert status == "translate"

    def test_lang_check_skipped_when_source_auto(self):
        """When SOURCE_LANG=auto, all languages pass."""
        doc = _make_doc(content="Hello world")
        client = _mock_client(doc)
        with patch("backfill.SOURCE_LANG", "auto"):
            with patch("backfill.detect_language", return_value="en") as mock_detect:
                status, _ = _check(client, 1, self.AUTO_TAG_ID, self.TRANS_FIELD_ID)
        # detect_language should not have been called
        mock_detect.assert_not_called()
        assert status == "translate"


# ---------------------------------------------------------------------------
# run() — dry-run does not call handle()
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_dry_run_does_not_call_handle(self, capsys):
        """With --dry-run, webhook.handle must never be called."""
        # Patch the shared lookup calls in run()
        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client

            # Tags lookup (get_tag_id_by_name)
            tag_resp = MagicMock()
            tag_resp.raise_for_status = MagicMock()
            tag_resp.json.return_value = {"results": []}
            # Custom fields lookup (get_custom_field_ids)
            fields_resp = MagicMock()
            fields_resp.raise_for_status = MagicMock()
            fields_resp.json.return_value = {"results": [{"id": 10, "name": FIELD_TRANSLATION}]}
            # Doc fetch — one eligible doc
            doc_resp = MagicMock()
            doc_resp.raise_for_status = MagicMock()
            doc_resp.json.return_value = _make_doc(doc_id=1, title="Test")

            mock_client.get.side_effect = [tag_resp, fields_resp, doc_resp]

            with patch.object(webhook, "handle") as mock_handle:
                with patch("backfill.SOURCE_LANG", "auto"):
                    run(start=1, end=1, dry_run=True, delay=0)

        mock_handle.assert_not_called()
        out = capsys.readouterr().out
        assert "dry run" in out.lower()


# ---------------------------------------------------------------------------
# run() — emit capture and result counts
# ---------------------------------------------------------------------------

class TestRunEmitCapture:
    def _run_with_mock_check(self, check_results: list, handle_emits: list[dict]) -> tuple[str, str]:
        """
        Run run() with _check() returning check_results in order and
        webhook.handle() emitting handle_emits in order.
        Returns (stdout, stderr).
        """
        check_iter = iter(check_results)

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client

            # Shared lookups for run() preamble
            tag_resp = MagicMock()
            tag_resp.raise_for_status = MagicMock()
            tag_resp.json.return_value = {"results": []}
            fields_resp = MagicMock()
            fields_resp.raise_for_status = MagicMock()
            fields_resp.json.return_value = {"results": [{"id": 10, "name": FIELD_TRANSLATION}]}
            mock_client.get.side_effect = [tag_resp, fields_resp]

            emit_iter = iter(handle_emits)

            def fake_handle(doc_id, content):
                try:
                    entry = next(emit_iter)
                    webhook.emit(entry)
                except StopIteration:
                    pass

            with patch("backfill._check", side_effect=check_iter):
                with patch.object(webhook, "handle", side_effect=fake_handle):
                    import io
                    from contextlib import redirect_stdout
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        run(start=1, end=1, dry_run=False, delay=0)
                    return buf.getvalue(), ""

    def test_translated_result_shown(self):
        check = [("translate", "Doc A")]
        emits = [{"action": "translated", "source_id": 1, "source_title": "Doc A",
                  "uploaded": [{"fmt": "pdf", "id": 99, "title": "[EN] Doc A"}]}]
        out, _ = self._run_with_mock_check(check, emits)
        assert "✓" in out
        assert "[EN] Doc A" in out

    def test_skipped_result_shown(self):
        check = [("translate", "Doc B")]
        emits = [{"action": "skipped", "source_id": 1, "reason": "already translated"}]
        out, _ = self._run_with_mock_check(check, emits)
        assert "skipped" in out

    def test_failed_result_shown(self):
        check = [("translate", "Doc C")]
        emits = [{"action": "failed", "source_id": 1, "reason": "HTTP 500"}]
        out, _ = self._run_with_mock_check(check, emits)
        assert "✗" in out
        assert "HTTP 500" in out

    def test_translated_with_link_errors_shown(self):
        check = [("translate", "Doc D")]
        emits = [{"action": "translated", "source_id": 1, "source_title": "Doc D",
                  "uploaded": [{"fmt": "pdf", "id": 99, "title": "[EN] Doc D"}],
                  "errors": ["custom field 'translation' not found"]}]
        out, _ = self._run_with_mock_check(check, emits)
        assert "link errors" in out
