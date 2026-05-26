# 🧪 Testing

## ⚙️ Setup

Install dev dependencies (includes `pytest`):

```bash
pip install -r requirements-dev.txt
```

## ▶️ Running tests

Run the full suite from the project root:

```bash
python -m pytest tests/ -v
```

Run a single file:

```bash
python -m pytest tests/test_api.py -v
```

Run a single test by name:

```bash
python -m pytest tests/test_backends.py::TestOllamaCall::test_prompt_substitution -v
```

## 📋 Test files

| File | What it covers |
|---|---|
| `tests/test_fonts.py` | Font name normalisation, family detection, bold/italic style resolution, `resolve_font` tiers 1–3 |
| `tests/test_config.py` | `load_config` default merging, `save_config` roundtrip, `update_config` partial update |
| `tests/test_backends.py` | `call()` and `test_connection()` for Google, LibreTranslate, and Ollama — all HTTP calls mocked |
| `tests/test_api.py` | FastAPI routes via `TestClient`: health, config GET/PATCH, cancel, translate (success and error paths) |
| `tests/test_ocr_utils.py` | Tesseract OCR dispatch, LLM OCR (httpx mocked via `sys.modules`), scanned-page detection |

## 📝 Notes

- No live services required — all HTTP calls are mocked via `unittest.mock` or `sys.modules` patching.
- `test_api.py` uses a mocked `translate_sync` — no actual PDF translation runs.
- `tests/conftest.py` provides shared fixtures: `isolated_config` (redirects `config.json` to a temp file), `minimal_pdf` (one-page fitz PDF), and `reset_job` (clears the `_job` singleton between API tests).
- 114 tests total; all pass with no live services.
