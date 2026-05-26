# 🔌 pdf-translate — API Operational Guide

Interactive API docs (Swagger UI) are available at **`http://localhost:7860/docs`** while the app is running. This document covers operational concerns not captured by Swagger: timeouts, queue behaviour, the cancellation workflow, and integration examples.

---

## 📋 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Service status + current job state |
| `GET` | `/api/config` | Read backend configuration (keys masked) |
| `PATCH` | `/api/config` | Partially update backend configuration |
| `POST` | `/api/translate` | Translate a PDF |
| `DELETE` | `/api/translate` | Cancel the running translation |

---

## ⏱️ Synchronous behaviour and timeouts

`POST /api/translate` is **synchronous** — the HTTP connection stays open until the full translation completes, then the response body (PDF / zip / HTML) is returned. There is no streaming progress; the client simply waits.

### Timeout layers

Three independent timeouts are in play:

| Layer | Where configured | Default | Notes |
|-------|-----------------|---------|-------|
| Per-block backend call | `TRANSLATE_TIMEOUT` in `config.py` | 30 s | Applied to each API call to Ollama / LibreTranslate / Google. If one block times out, the whole job fails with 500. |
| Client-side | In your script / curl | varies | The caller decides how long to wait. Must be set explicitly. |
| Reverse proxy | nginx / Caddy config | 60 s (nginx default) | **Common gotcha.** nginx closes the connection after 60 s even if the client timeout is higher. |

### Recommended client timeout

Set your client timeout to comfortably exceed the expected translation time. For a 50-page document with Ollama, 5–10 minutes is reasonable:

```python
# httpx (Python)
httpx.post("http://localhost:7860/api/translate", ..., timeout=600.0)
```

```bash
# curl
curl --max-time 600 ...
```

### nginx configuration (reverse proxy)

If running behind nginx, raise the proxy read timeout to match:

```nginx
location / {
    proxy_pass         http://127.0.0.1:7860;
    proxy_read_timeout 600;
    proxy_send_timeout 600;
}
```

---

## 🔄 Queue

One translation runs at a time. One additional request may wait.

| State | What happens |
|-------|-------------|
| No job running | Request starts immediately |
| 1 job running, 0 waiting | Request waits (holds connection open) |
| 1 job running, 1 waiting | Request receives **429** with `Retry-After: 60` |

The `GET /api/health` response includes `job.queued` — the number of requests currently waiting.

---

## 🔍 What to do after a client timeout

When your client times out, the server **keeps running the translation** — it has no way to detect the lost connection. Use the health endpoint to find out what happened:

```bash
curl http://localhost:7860/api/health
```

### Possible outcomes

**Job still running** — translation is still in progress:
```json
{
  "job": {
    "running": true,
    "queued": 0,
    "started_at": "2026-05-24T14:32:01Z",
    "source": "nl",
    "target": "en",
    "backend": "Ollama"
  }
}
```
Options: wait longer (check again later), or cancel with `DELETE /api/translate`.

**Job completed after your timeout** — translation finished; result was not delivered:
```json
{
  "job": {
    "running": false,
    "last": {
      "status": "completed",
      "started_at": "2026-05-24T14:32:01Z",
      "completed_at": "2026-05-24T14:34:18Z",
      "source": "nl",
      "target": "en",
      "backend": "Ollama"
    }
  }
}
```
Result is gone (temp file was served and cleaned up). **Retry the POST** — the queue is free.

**Job failed** — backend error:
```json
{
  "job": {
    "running": false,
    "last": {
      "status": "failed",
      "completed_at": "2026-05-24T14:33:05Z",
      "error": "Translation failed: Connection refused to Ollama"
    }
  }
}
```
Fix the backend issue, then retry.

`last` persists until the next job starts. It is reset to `null` on first run after a restart.

---

## 🛑 Cancellation

To stop a stuck or long-running translation:

```bash
curl -X DELETE http://localhost:7860/api/translate
```

Response:
```json
{
  "status": "ok",
  "message": "Cancellation requested. Translation stops after the current block."
}
```

**Best-effort:** the current block's backend call (Ollama / LibreTranslate / Google) runs to completion before the pipeline stops. Cancellation is detected between blocks, not mid-call. For large Ollama models, this may take up to `TRANSLATE_TIMEOUT` (30 s) to take effect.

After cancellation, the blocked `POST /api/translate` call returns **409**. The health endpoint reports `last.status: "cancelled"`.

---

## ⚙️ Processing options

The following form fields control how the PDF is scanned and prepared before translation. All are optional; defaults match the UI defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_wrap` | bool | `false` | Collapse line breaks within text blocks before translation. Helps when the PDF has hard line breaks inside paragraphs. Keep off for lists, tables, and code blocks where breaks are meaningful. |
| `filter_icons` | bool | `true` | Strip single-character lines likely to be icon glyphs from mixed text blocks. Disable only if legitimate single-character content (section letters, bullets) is being dropped. |
| `merge_blocks` | bool | `false` | Merge same-line adjacent fragments into one block before translation. Enable for DTP/InDesign PDFs where each word is a separate text object (symptom: many small blocks per page, 429 errors on self-hosted backends). Not recommended for multi-column layouts. |
| `detect_tables` | bool | `true` | Use PyMuPDF's table detector to identify table cells and apply shrink-to-fit text fitting. Disable if translated text appears abnormally small — this indicates a false-positive table detection. |
| `force_ocr` | bool | `false` | Ignore the existing text layer entirely and OCR every page as an image (using the backend's OCR engine). Use for mixed PDFs where digital and scanned content are interleaved and translating the text layer alone gives incomplete results. Requires OCR dependencies (Tesseract or reachable Ollama vision model). |

---

## 📦 Output formats

The `outputs` form field controls what is returned:

| Value | Content-Type | Description |
|-------|-------------|-------------|
| `pdf` (default) | `application/pdf` | Translated PDF only |
| `sbs` | `application/pdf` | Side-by-side landscape PDF, layout-matched (original \| translation) |
| `reading` | `application/pdf` | Side-by-side landscape PDF, clean text (original \| translation) |
| `all` | `application/zip` | Zip containing all three files |

The translated PDF always has a selectable text layer (not a scanned image). When uploaded to Paperless-ngx, Paperless will index the translated text.

---

## 🔧 Configuration

### Read current config

```bash
curl http://localhost:7860/api/config
```

API keys are masked as `"***"` in the response.

### Update config

```bash
curl -X PATCH http://localhost:7860/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "backend": "Ollama",
    "ollama_url": "http://your-ollama-host:11434",
    "ollama_model": "translategemma:latest"
  }'
```

Only supplied fields are changed. Changes persist to `config.json` immediately — no restart needed. The Gradio UI reflects the new values on next page load.

---

## ❌ Error reference

### HTTP status codes

| Status | Meaning | Retry? |
|--------|---------|--------|
| `200` | Success — response body is the output file | — |
| `409` | Translation was cancelled via `DELETE /api/translate` | Retry if desired |
| `422` | No translatable text found in the PDF | Enable `force_ocr=true` and retry |
| `429` | Queue full (2 jobs already running/waiting) | Wait `Retry-After` seconds, then retry |
| `500` | Translation or OCR failure | See error detail below |

### 500 error detail strings

When a `500` is returned, the JSON body contains both a human-readable message and a machine-readable type:

```json
{
  "detail": "Translation failed: <message>",
  "error_type": "ocr_model_error"
}
```

Branch on `error_type` in automation scripts. The possible values and their meaning:

| `error_type` | Failure class | Recommended action |
|--------------|---------------|--------------------|
| `ocr_model_error` | OCR model assertion error (GGML_ASSERT) — model incompatible with this page | `PATCH /api/config` with a different `ocr_ollama_model` (e.g. `deepseek-ocr` or `minicpm-v`), then retry |
| `ocr_oom` | OCR model ran out of GPU memory (CUDA OOM) | `PATCH /api/config` with a smaller `ocr_ollama_model` (e.g. `glm-ocr`), then retry |
| `translation_rate_limit` | Translation backend rate-limited — all retries exhausted | Wait 60 s, then retry |
| `config_error` | Backend unreachable (connection refused) | Check service config, do not retry blindly |
| `translation_error` | Other translation or pipeline failure | Check `detail` message; may be transient |
| `no_text` | No translatable text found (returned as 422) | Enable `force_ocr=true`, then retry |
| `cancelled` | Job cancelled via `DELETE /api/translate` (returned as 409) | Retry if desired |

### Decision tree for automation

```
POST /api/translate
├── 200 → done ✓
├── 422 → PATCH force_ocr=true → retry once
│           still 422 → skip document (no text at all)
├── 429 (queue full) → sleep Retry-After → retry
├── 500 + "GGML_ASSERT"
│       → PATCH ocr_ollama_model=deepseek-ocr → retry once
│           still 500 → fall back to Tesseract: PATCH ocr_service=Tesseract → retry once
│               still 500 → alert + skip
├── 500 + "CUDA OOM"
│       → PATCH ocr_ollama_model=glm-ocr → retry once
│           still 500 → PATCH ocr_service=Tesseract → retry once
├── 500 + "429 TOO MANY REQUESTS"
│       → sleep 60 s → retry
└── 500 (other) → alert + skip
```

`PATCH /api/config` changes persist to `config.json` immediately — subsequent translation calls use the new values.

---

## 📄 Paperless-ngx integration

### Post-consumption script

Paperless runs post-consumption scripts after a document is ingested. The script receives the document path via `DOCUMENT_WORKING_PATH`.

The script below implements the full error decision tree: it retries with a fallback OCR model on GGML_ASSERT failures, enables force OCR on 422, and retries after rate-limit exhaustion.

```python
#!/usr/bin/env python3
"""
Translate an ingested PDF to English via pdf-translate API.
Implements retry logic for OCR model failures and rate limiting.

Paperless registration:
  PAPERLESS_POST_CONSUME_SCRIPT=/path/to/translate_script.py
"""

import os
import sys
import time
from pathlib import Path

import httpx

API_BASE   = "http://localhost:7860"
TARGET_LANG = "en"
TIMEOUT     = 600.0  # seconds — raise for large documents or slow backends

# OCR model fallback chain: try each in order on GGML_ASSERT or OOM
OCR_MODEL_FALLBACKS = ["glm-ocr", "deepseek-ocr", "minicpm-v"]


def patch_config(client: httpx.Client, **fields) -> None:
    client.patch(f"{API_BASE}/api/config", json=fields, timeout=10.0).raise_for_status()


def translate(client: httpx.Client, pdf_path: str, **extra_fields) -> bytes:
    with open(pdf_path, "rb") as f:
        r = client.post(
            f"{API_BASE}/api/translate",
            files={"file": (Path(pdf_path).name, f, "application/pdf")},
            data={"source": "auto", "target": TARGET_LANG, "outputs": "pdf", **extra_fields},
            timeout=TIMEOUT,
        )
    r.raise_for_status()
    return r.content


def run(pdf_path: str) -> None:
    with httpx.Client() as client:
        # --- Attempt 1: current config ---
        try:
            result = translate(client, pdf_path)
            Path(pdf_path).write_bytes(result)
            print(f"[pdf-translate] OK: {pdf_path}")
            return
        except httpx.HTTPStatusError as e:
            status     = e.response.status_code
            body       = e.response.json()
            detail     = body.get("detail", "")
            error_type = body.get("error_type", "translation_error")
            print(f"[pdf-translate] attempt 1 failed — HTTP {status} ({error_type}): {detail}", file=sys.stderr)
        except httpx.TimeoutException:
            print("[pdf-translate] attempt 1 timed out — check GET /api/health", file=sys.stderr)
            sys.exit(1)

        # --- 422 / no_text: enable force OCR and retry ---
        if status == 422 or error_type == "no_text":
            print("[pdf-translate] no text layer — retrying with force_ocr=true", file=sys.stderr)
            patch_config(client, force_ocr=True)
            try:
                result = translate(client, pdf_path)
                Path(pdf_path).write_bytes(result)
                print(f"[pdf-translate] OK (force_ocr): {pdf_path}")
                return
            except httpx.HTTPStatusError as e:
                print(f"[pdf-translate] force_ocr retry failed: {e.response.text}", file=sys.stderr)
                sys.exit(1)

        # --- ocr_model_error / ocr_oom: cycle through OCR model fallbacks ---
        if error_type in ("ocr_model_error", "ocr_oom"):
            for model in OCR_MODEL_FALLBACKS:
                print(f"[pdf-translate] OCR model error — retrying with ocr_ollama_model={model}", file=sys.stderr)
                patch_config(client, ocr_ollama_model=model)
                try:
                    result = translate(client, pdf_path)
                    Path(pdf_path).write_bytes(result)
                    print(f"[pdf-translate] OK (ocr_model={model}): {pdf_path}")
                    return
                except httpx.HTTPStatusError as e:
                    error_type = e.response.json().get("error_type", "translation_error")
                    if error_type not in ("ocr_model_error", "ocr_oom"):
                        break  # different error — stop model cycling
                    continue
            # All Ollama OCR models failed — fall back to Tesseract
            print("[pdf-translate] all Ollama OCR models failed — retrying with Tesseract", file=sys.stderr)
            patch_config(client, ocr_service="Tesseract")
            try:
                result = translate(client, pdf_path)
                Path(pdf_path).write_bytes(result)
                print(f"[pdf-translate] OK (Tesseract): {pdf_path}")
                return
            except httpx.HTTPStatusError as e:
                print(f"[pdf-translate] Tesseract fallback failed: {e.response.text}", file=sys.stderr)
                sys.exit(1)

        # --- translation_rate_limit: wait and retry once ---
        if error_type == "translation_rate_limit":
            print("[pdf-translate] rate limit — waiting 60 s before retry", file=sys.stderr)
            time.sleep(60)
            try:
                result = translate(client, pdf_path)
                Path(pdf_path).write_bytes(result)
                print(f"[pdf-translate] OK (after rate-limit wait): {pdf_path}")
                return
            except httpx.HTTPStatusError as e:
                print(f"[pdf-translate] retry after rate-limit failed: {e.response.text}", file=sys.stderr)
                sys.exit(1)

        # --- 429 queue full: wait Retry-After and retry ---
        if status == 429:
            retry_after = int(e.response.headers.get("Retry-After", 60))
            print(f"[pdf-translate] queue full — waiting {retry_after} s", file=sys.stderr)
            time.sleep(retry_after)
            try:
                result = translate(client, pdf_path)
                Path(pdf_path).write_bytes(result)
                print(f"[pdf-translate] OK (after queue wait): {pdf_path}")
                return
            except httpx.HTTPStatusError as e:
                print(f"[pdf-translate] retry after queue wait failed: {e.response.text}", file=sys.stderr)
                sys.exit(1)

        # --- Unhandled failure ---
        print(f"[pdf-translate] unhandled error — skipping document", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    pdf_path = os.environ.get("DOCUMENT_WORKING_PATH")
    if not pdf_path:
        sys.exit(0)
    run(pdf_path)
```

### Notes

- The script blocks until translation completes. Paperless waits for the script to exit before marking the document as fully consumed.
- If the script exits non-zero, Paperless logs an error but still ingests the document (untranslated).
- `DOCUMENT_WORKING_PATH` is the working copy of the document. Overwriting it replaces the stored file with the translated version.
- `patch_config` changes persist to `config.json`. If multiple documents are processed concurrently (unlikely with the queue), config changes may interfere. The queue (max 1 waiting) provides a natural serialisation barrier.
- For selective translation (only certain languages or document types), check `DOCUMENT_ORIGINAL_FILENAME` or use Paperless workflows to conditionally trigger the script.

---

## ⚡ Quick reference

```bash
# Health check
curl http://localhost:7860/api/health

# Translate a PDF (Google, default output = translated PDF)
curl -X POST http://localhost:7860/api/translate \
  -F "file=@document.pdf" \
  -F "source=nl" -F "target=en" \
  --output translated.pdf

# Translate with Ollama, get zip of all outputs
curl -X POST http://localhost:7860/api/translate \
  -F "file=@document.pdf" \
  -F "source=nl" -F "target=en" \
  -F "service=Ollama" \
  -F "outputs=all" \
  --output outputs.zip

# DTP/InDesign PDF with many fragmented blocks — merge same-line fragments
curl -X POST http://localhost:7860/api/translate \
  -F "file=@dtp-document.pdf" \
  -F "source=de" -F "target=en" \
  -F "merge_blocks=true" \
  --output translated.pdf

# Mixed PDF (scanned + digital) — force OCR on all pages
curl -X POST http://localhost:7860/api/translate \
  -F "file=@mixed-scan.pdf" \
  -F "source=de" -F "target=en" \
  -F "force_ocr=true" \
  --output translated.pdf

# Cancel running translation
curl -X DELETE http://localhost:7860/api/translate

# Read config
curl http://localhost:7860/api/config

# Switch to LibreTranslate
curl -X PATCH http://localhost:7860/api/config \
  -H "Content-Type: application/json" \
  -d '{"backend": "LibreTranslate", "libre_url": "http://your-libretranslate-host:5000"}'
```
