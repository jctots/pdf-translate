"""
Shared fixtures for pdf-translate tests.
"""

import sys
from pathlib import Path

import fitz
import pytest

# Make the project root importable from within tests/
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Config path isolation
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """
    Redirect config.CONFIG_PATH to a temp file so tests never touch the real
    config.json.  Returns the Path so tests can pre-populate it if needed.
    """
    import config as cfg_module
    fake_path = tmp_path / "config.json"
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# Minimal valid PDF factory
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_pdf(tmp_path) -> Path:
    """Write a one-page PDF with a single text block; return its path."""
    path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world.", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Job state reset (for API tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def reset_job():
    """Reset the module-level _job singleton before each API test."""
    import app
    import threading
    app._job.running = False
    app._job.queued = 0
    app._job.started_at = None
    app._job.source = None
    app._job.target = None
    app._job.backend = None
    app._job.cancel_event = threading.Event()
    app._job.last_status = None
    app._job.last_started_at = None
    app._job.last_completed_at = None
    app._job.last_source = None
    app._job.last_target = None
    app._job.last_backend = None
    app._job.last_error = None
    yield
    # Reset again after test in case it mutated state
    app._job.running = False
    app._job.cancel_event = threading.Event()
