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
        # Create fake output PDFs to return
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs = tmp_path / "sbs.pdf"
        fake_reading = tmp_path / "reading.pdf"
        fake_translated.write_bytes(b"%PDF-1.4 fake")
        fake_sbs.write_bytes(b"%PDF-1.4 fake sbs")
        fake_reading.write_bytes(b"%PDF-1.4 fake reading")

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
        fake_reading = tmp_path / "reading.pdf"
        for p in (fake_translated, fake_sbs, fake_reading):
            p.write_bytes(b"%PDF-1.4 fake")

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
        fake_reading    = tmp_path / "reading.pdf"
        for p in (fake_translated, fake_sbs, fake_reading):
            p.write_bytes(b"%PDF-1.4 fake")

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

    def test_processing_options_forwarded(self, reset_job, minimal_pdf, tmp_path):
        """merge_blocks, detect_tables, force_ocr values are forwarded to translate_sync."""
        from unittest.mock import MagicMock
        fake_translated = tmp_path / "translated.pdf"
        fake_sbs        = tmp_path / "sbs.pdf"
        fake_reading    = tmp_path / "reading.pdf"
        for p in (fake_translated, fake_sbs, fake_reading):
            p.write_bytes(b"%PDF-1.4 fake")

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
