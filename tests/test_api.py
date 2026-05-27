"""
Integration tests for the FastAPI REST API.

Uses TestClient(api_app) — no live server or real translation needed.
POST /api/translate uses a mocked translate_sync to avoid PDF processing.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

# Import api_app (FastAPI instance) directly — avoids starting uvicorn
from app import api_app

client = TestClient(api_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _page_count
# ---------------------------------------------------------------------------

class TestPageCount:
    def test_none_returns_1(self):
        from app import _page_count
        assert _page_count(None) == 1

    def test_invalid_path_returns_1(self):
        from app import _page_count
        assert _page_count("/nonexistent/path/file.pdf") == 1

    def test_valid_pdf_returns_page_count(self, minimal_pdf):
        from app import _page_count
        assert _page_count(str(minimal_pdf)) == 1


# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    def test_ocr_oom_error_type(self):
        from app import _classify_error
        from exceptions import OcrOomError
        assert _classify_error("", OcrOomError("oom")) == "ocr_oom"

    def test_ocr_model_error_type(self):
        from app import _classify_error
        from exceptions import OcrModelError
        assert _classify_error("", OcrModelError("err")) == "ocr_model_error"

    def test_rate_limit_error_type(self):
        from app import _classify_error
        from exceptions import RateLimitError
        assert _classify_error("", RateLimitError("rate")) == "translation_rate_limit"

    def test_backend_connection_error_type(self):
        from app import _classify_error
        from exceptions import BackendConnectionError
        assert _classify_error("", BackendConnectionError("conn")) == "config_error"

    def test_fallback_string_cuda_oom(self):
        from app import _classify_error
        assert _classify_error("CUDA OOM happened") == "ocr_oom"

    def test_fallback_string_connection_refused(self):
        from app import _classify_error
        assert _classify_error("Connection refused") == "config_error"

    def test_fallback_string_unknown(self):
        from app import _classify_error
        assert _classify_error("something completely different") == "translation_error"

    def test_typed_exc_takes_priority_over_message(self):
        """Type check must win even if message contains a different pattern."""
        from app import _classify_error
        from exceptions import OcrOomError
        # Message says "connection refused" but type is OcrOomError → type wins
        assert _classify_error("Connection refused", OcrOomError("oom")) == "ocr_oom"


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_status_ok(self, reset_job):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"

    def test_version_present(self, reset_job):
        r = client.get("/api/health")
        assert "version" in r.json()

    def test_backend_present(self, reset_job):
        r = client.get("/api/health")
        assert "backend" in r.json()

    def test_job_idle_shape(self, reset_job):
        r = client.get("/api/health")
        job = r.json()["job"]
        assert job["running"] is False
        assert "last" in job  # may be None if no job has run

    def test_job_running_state(self, reset_job):
        import app
        app._job.running = True
        app._job.started_at = "2026-01-01T00:00:00+00:00"
        app._job.source = "nl"
        app._job.target = "en"
        app._job.backend = "Google"
        app._job.queued = 0
        r = client.get("/api/health")
        job = r.json()["job"]
        assert job["running"] is True
        assert job["source"] == "nl"
        assert job["target"] == "en"
        assert job["backend"] == "Google"


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------

class TestConfigGet:
    def test_returns_200(self):
        r = client.get("/api/config")
        assert r.status_code == 200

    def test_backend_key_present(self):
        r = client.get("/api/config")
        assert "backend" in r.json()

    def test_keys_masked_when_set(self, isolated_config):
        import json, config as cfg
        # Write a config with API keys
        isolated_config.write_text(json.dumps({
            **cfg.DEFAULT_CONFIG,
            "ollama_key": "my-ollama-secret",
            "libre_key": "my-libre-secret",
        }))
        r = client.get("/api/config")
        body = r.json()
        assert body["ollama_key"] == "***"
        assert body["libre_key"] == "***"

    def test_empty_keys_not_masked(self, isolated_config):
        import json, config as cfg
        isolated_config.write_text(json.dumps(cfg.DEFAULT_CONFIG))
        r = client.get("/api/config")
        body = r.json()
        assert body["ollama_key"] == ""
        assert body["libre_key"] == ""


# ---------------------------------------------------------------------------
# PATCH /api/config
# ---------------------------------------------------------------------------

class TestConfigPatch:
    def test_valid_update_returns_200(self, isolated_config):
        r = client.patch("/api/config", json={"backend": "Ollama"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "backend" in body["updated"]

    def test_empty_body_returns_400(self):
        r = client.patch("/api/config", json={})
        assert r.status_code == 400

    def test_null_fields_ignored(self, isolated_config):
        # Pydantic model treats omitted/null fields as None; they must be ignored
        r = client.patch("/api/config", json={"backend": "Google", "ollama_key": None})
        assert r.status_code == 200
        body = r.json()
        # ollama_key is None → not updated
        assert "ollama_key" not in body["updated"]

    def test_update_persisted(self, isolated_config):
        client.patch("/api/config", json={"backend": "LibreTranslate"})
        r = client.get("/api/config")
        assert r.json()["backend"] == "LibreTranslate"


# ---------------------------------------------------------------------------
# DELETE /api/translate (cancel)
# ---------------------------------------------------------------------------

class TestCancel:
    def test_no_running_job_returns_404(self, reset_job):
        r = client.delete("/api/translate")
        assert r.status_code == 404

    def test_running_job_sets_cancel_event(self, reset_job):
        import app
        app._job.running = True
        r = client.delete("/api/translate")
        assert r.status_code == 200
        assert app._job.cancel_event.is_set()

    def test_cancel_response_body(self, reset_job):
        import app
        app._job.running = True
        r = client.delete("/api/translate")
        body = r.json()
        assert body["status"] == "ok"
        assert "Cancellation" in body["message"] or "cancel" in body["message"].lower()


# ---------------------------------------------------------------------------
# POST /api/translate
# ---------------------------------------------------------------------------

class TestTranslate:
    def test_missing_file_returns_422(self, reset_job):
        r = client.post("/api/translate", data={"source": "en", "target": "nl"})
        assert r.status_code == 422

    def test_invalid_outputs_param_returns_422(self, reset_job, minimal_pdf):
        r = client.post(
            "/api/translate",
            files={"file": ("test.pdf", minimal_pdf.read_bytes(), "application/pdf")},
            data={"source": "en", "target": "nl", "outputs": "invalid_value"},
        )
        assert r.status_code == 422

    def test_success_returns_pdf(self, reset_job, minimal_pdf, tmp_path):
        # Create fake output files to return
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs = tmp_path / "sbs.pdf"
        fake_reading = tmp_path / "reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake sbs")
        fake_reading.write_text("<html><body>reading</body></html>", encoding="utf-8")

        with patch("app.translate_sync", return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        )):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl", "outputs": "pdf"},
            )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers.get("x-source-lang") == "en"
        assert r.headers.get("x-target-lang") == "nl"

    def test_success_sbs_output(self, reset_job, minimal_pdf, tmp_path):
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs = tmp_path / "sbs.pdf"
        fake_reading = tmp_path / "reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake sbs")
        fake_reading.write_text("<html><body>reading</body></html>", encoding="utf-8")

        with patch("app.translate_sync", return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        )):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl", "outputs": "sbs"},
            )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"

    def test_success_reading_output(self, reset_job, minimal_pdf, tmp_path):
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs = tmp_path / "sbs.pdf"
        fake_reading = tmp_path / "doc_en_nl_reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake sbs")
        fake_reading.write_text("<html><body>reading view</body></html>", encoding="utf-8")

        with patch("app.translate_sync", return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        )):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl", "outputs": "reading"},
            )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert r.headers.get("content-disposition", "").endswith("_reading.html\"")

    def test_no_translatable_blocks_returns_422(self, reset_job, minimal_pdf):
        with patch("app.translate_sync", side_effect=ValueError("No translatable text blocks found")):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl"},
            )
        assert r.status_code == 422

    def test_backend_error_returns_500(self, reset_job, minimal_pdf):
        with patch("app.translate_sync", side_effect=RuntimeError("Ollama connection refused")):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl"},
            )
        assert r.status_code == 500

    def test_queue_full_returns_429(self, reset_job, minimal_pdf):
        import app
        # Simulate: 1 running + 1 already waiting
        app._job.running = True
        app._job.queued = 1
        r = client.post(
            "/api/translate",
            files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
            data={"source": "en", "target": "nl"},
        )
        assert r.status_code == 429
        assert r.headers.get("retry-after") == "60"

    def test_processing_options_accepted(self, reset_job, minimal_pdf, tmp_path):
        """merge_blocks, detect_tables=false, force_ocr are accepted without 422."""
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs        = tmp_path / "sbs.pdf"
        fake_reading    = tmp_path / "reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake")
        fake_reading.write_text("<html></html>", encoding="utf-8")

        with patch("app.translate_sync", return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        )):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={
                    "source": "en", "target": "nl",
                    "merge_blocks": "true",
                    "detect_tables": "false",
                    "force_ocr": "true",
                },
            )
        assert r.status_code == 200

    def test_upload_too_large_returns_413(self, reset_job):
        import app
        original = app._MAX_UPLOAD_BYTES
        app._MAX_UPLOAD_BYTES = 10  # 10 bytes — tiny limit for test
        try:
            r = client.post(
                "/api/translate",
                files={"file": ("big.pdf", b"x" * 100, "application/pdf")},
                data={"source": "en", "target": "nl"},
            )
        finally:
            app._MAX_UPLOAD_BYTES = original
        assert r.status_code == 413

    def test_output_dir_cleaned_up_after_response(self, reset_job, minimal_pdf, tmp_path):
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs        = tmp_path / "sbs.pdf"
        fake_reading    = tmp_path / "reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake sbs")
        fake_reading.write_text("<html></html>", encoding="utf-8")

        with patch("app.translate_sync", return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        )):
            with patch("app.shutil.rmtree") as mock_rmtree:
                r = client.post(
                    "/api/translate",
                    files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                    data={"source": "en", "target": "nl"},
                )
        assert r.status_code == 200
        mock_rmtree.assert_called_once()
        cleaned_path = mock_rmtree.call_args[0][0]
        assert str(tmp_path) in cleaned_path

    def test_processing_options_forwarded(self, reset_job, minimal_pdf, tmp_path):  # noqa: E301
        """merge_blocks, detect_tables, force_ocr values are forwarded to translate_sync."""
        from unittest.mock import MagicMock
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs        = tmp_path / "sbs.pdf"
        fake_reading    = tmp_path / "reading.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake")
        fake_reading.write_text("<html></html>", encoding="utf-8")

        mock_sync = MagicMock(return_value=(
            str(fake_translated), str(fake_sbs), str(fake_reading)
        ))
        with patch("app.translate_sync", mock_sync):
            client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={
                    "source": "en", "target": "nl",
                    "merge_blocks": "true",
                    "detect_tables": "false",
                    "force_ocr": "true",
                },
            )

        _, kwargs = mock_sync.call_args
        assert kwargs.get("merge_blocks") is True
        assert kwargs.get("detect_tables") is False
        assert kwargs.get("force_ocr") is True


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

class TestApiKey:
    """PDF_TRANSLATE_API_KEY env var gates protected endpoints; health stays open."""

    def test_no_key_configured_allows_translate(self, reset_job, minimal_pdf, tmp_path, monkeypatch):
        monkeypatch.delenv("PDF_TRANSLATE_API_KEY", raising=False)
        fake_translated = tmp_path / "t.pdf"
        fake_sbs        = tmp_path / "s.pdf"
        fake_reading    = tmp_path / "r.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake")
        fake_reading.write_text("<html></html>", encoding="utf-8")
        with patch("app.translate_sync", return_value=(str(fake_translated), str(fake_sbs), str(fake_reading))):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl"},
            )
        assert r.status_code == 200

    def test_valid_key_allows_translate(self, reset_job, minimal_pdf, tmp_path, monkeypatch):
        monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret123")
        fake_translated = tmp_path / "t.pdf"
        fake_sbs        = tmp_path / "s.pdf"
        fake_reading    = tmp_path / "r.html"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake")
        fake_reading.write_text("<html></html>", encoding="utf-8")
        with patch("app.translate_sync", return_value=(str(fake_translated), str(fake_sbs), str(fake_reading))):
            r = client.post(
                "/api/translate",
                files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
                data={"source": "en", "target": "nl"},
                headers={"Authorization": "Bearer secret123"},
            )
        assert r.status_code == 200

    def test_wrong_key_returns_401_on_translate(self, reset_job, minimal_pdf, monkeypatch):
        monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret123")
        r = client.post(
            "/api/translate",
            files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
            data={"source": "en", "target": "nl"},
            headers={"Authorization": "Bearer wrongkey"},
        )
        assert r.status_code == 401

    def test_missing_auth_header_returns_401(self, reset_job, minimal_pdf, monkeypatch):
        monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret123")
        r = client.post(
            "/api/translate",
            files={"file": ("doc.pdf", minimal_pdf.read_bytes(), "application/pdf")},
            data={"source": "en", "target": "nl"},
        )
        assert r.status_code == 401

    def test_key_gates_config_patch(self, isolated_config, monkeypatch):
        monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret123")
        r = client.patch("/api/config", json={"backend": "Ollama"})
        assert r.status_code == 401
        r = client.patch("/api/config", json={"backend": "Ollama"},
                         headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200

    def test_health_always_open(self, reset_job, monkeypatch):
        monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret123")
        r = client.get("/api/health")
        assert r.status_code == 200
