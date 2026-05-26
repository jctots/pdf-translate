# 📋 Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] — 2026-05-26

### Added
- Gradio web UI: upload PDF, select backend and languages, preview translated pages
- Three translation backends: Google Translate (unofficial, zero-config), LibreTranslate (self-hosted), Ollama (local LLM)
- Three output formats: translated PDF, side-by-side landscape PDF, HTML reading view
- Three-tier font resolution: embedded full fonts → bundled Liberation TTFs → base14 fallback
- Table cell detection and shrink-to-fit text strategy (preserves table layout); opt-out via `detect_tables` flag
- Icon glyph filtering (single-character and ASCII-remapped icon fonts)
- Vertical text block skipping
- Text reflow option (collapses visual line breaks before translation)
- Merge split lines (`merge_blocks`) — merges same-line adjacent fragments before translation; for DTP/InDesign PDFs with word-level text objects
- Force OCR mode — ignore the text layer and OCR every page as an image:
  - Tesseract backend: layout-accurate, 300 DPI, paragraph-level extraction
  - Ollama vision backend: content-accurate, configurable model (`glm-ocr` default, 1.1 GB)
  - Disables `filter_icons`, `merge_blocks`, `detect_tables` when enabled
- FastAPI REST API alongside Gradio UI on the same port:
  - `POST /api/translate` — synchronous PDF translation
  - `DELETE /api/translate` — cancel running translation
  - `GET /api/health` — service status and job state
  - `GET /api/config` — read backend configuration
  - `PATCH /api/config` — partial config update
- `error_type` field in 500 error responses for automation decision trees (`ocr_model_error`, `ocr_oom`, `translation_rate_limit`, `config_error`, `translation_error`, `no_text`, `cancelled`)
- Job queue: max 1 running + 1 waiting; third request returns 429
- Best-effort cancellation via threading.Event (stops between blocks)
- LibreTranslate 429 retry with exponential backoff (3 attempts, 10 s / 30 s / 60 s delays)
- `LIBRETRANSLATE_BLOCK_DELAY_MS` environment variable — configurable inter-call delay (default 200 ms)
- `PDF_TRANSLATE_DEBUG=1` environment variable — writes a per-translation debug log to `data/debug_YYYYMMDD_HHMMSS_{stem}.log`
- Swagger UI at `/docs`, ReDoc at `/redoc`
- App icon and favicon (Material Design translate icon)
- Docker support: `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- Unit and API test suite (114 tests, no live services required)
- Bundled Liberation Sans/Serif/Mono TTFs (OFL licence)
