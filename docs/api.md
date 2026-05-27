# 🔌 pdf-translate — API Operational Guide

Interactive API docs (Swagger UI) are available at **`http://localhost:7860/docs`** while the app is running. This document covers operational concerns not captured by Swagger: timeouts, queue behaviour, the cancellation workflow, and integration examples.

## 📋 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Service status + current job state |
| `GET` | `/api/config` | Read backend configuration (keys masked) |
| `PATCH` | `/api/config` | Partially update backend configuration |
| `POST` | `/api/translate` | Translate a PDF |
| `DELETE` | `/api/translate` | Cancel the running translation |

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

## 🔄 Queue

One translation runs at a time. One additional request may wait.

| State | What happens |
|-------|-------------|
| No job running | Request starts immediately |
| 1 job running, 0 waiting | Request waits (holds connection open) |
| 1 job running, 1 waiting | Request receives **429** with `Retry-After: 60` |

The `GET /api/health` response includes `job.queued` — the number of requests currently waiting.

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

## ⚙️ Processing options

The following form fields control how the PDF is scanned and prepared before translation. All are optional; defaults match the UI defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_wrap` | bool | `false` | Collapse line breaks within text blocks before translation. Helps when the PDF has hard line breaks inside paragraphs. Keep off for lists, tables, and code blocks where breaks are meaningful. |
| `filter_icons` | bool | `true` | Strip single-character lines likely to be icon glyphs from mixed text blocks. Disable only if legitimate single-character content (section letters, bullets) is being dropped. |
| `merge_blocks` | bool | `false` | Merge same-line adjacent fragments into one block before translation. Enable for DTP/InDesign PDFs where each word is a separate text object (symptom: many small blocks per page, 429 errors on self-hosted backends). Not recommended for multi-column layouts. |
| `detect_tables` | bool | `true` | Use PyMuPDF's table detector to identify table cells and apply shrink-to-fit text fitting. Disable if translated text appears abnormally small — this indicates a false-positive table detection. |
| `force_ocr` | bool | `false` | Ignore the existing text layer entirely and OCR every page as an image (using the backend's OCR engine). Use for mixed PDFs where digital and scanned content are interleaved and translating the text layer alone gives incomplete results. Requires OCR dependencies (Tesseract or reachable Ollama vision model). |

## 📦 Output formats

The `outputs` form field controls what is returned:

| Value | Content-Type | Description |
|-------|-------------|-------------|
| `pdf` (default) | `application/pdf` | Translated PDF only |
| `sbs` | `application/pdf` | Side-by-side landscape PDF, layout-matched (original \| translation) |
| `reading` | `text/html` | HTML reading view — original and translated text side by side, clean and reflowable |
| `all` | `application/zip` | Zip containing all three files |

The translated PDF always has a selectable text layer (not a scanned image). When uploaded to Paperless-ngx, Paperless will index the translated text.

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

### Decision tree for custom integrations

> **Note:** The [Paperless-ngx webhook container](../paperless_webhook/README.md) does not implement retries — it logs failures and tags the original document `translation-failed` in Paperless. The tree below is a reference for clients building their own automation directly on the REST API.
>
> The REST API is stateless — `PATCH /api/config` only affects the Gradio UI. All settings must be passed as request parameters on each call.

```
POST /api/translate
├── 200 → done ✓
├── 422 → retry with force_ocr=true
│           still 422 → skip (no translatable text)
├── 429 (queue full) → sleep Retry-After → retry
├── 500 + "GGML_ASSERT"
│       → retry with ocr_ollama_model=deepseek-ocr
│           still 500 → retry with ocr_service=Tesseract
│               still 500 → alert + skip
├── 500 + "CUDA OOM"
│       → retry with ocr_ollama_model=glm-ocr
│           still 500 → retry with ocr_service=Tesseract
├── 500 + "429 TOO MANY REQUESTS"
│       → sleep 60 s → retry
└── 500 (other) → alert + skip
```

## 📄 Paperless-ngx integration

See [paperless_webhook/README.md](../paperless_webhook/README.md) for the complete Paperless-ngx setup guide — webhook container, one-time Paperless configuration, environment variables, docker-compose snippet, and log format.

## ⚡ Quick reference

```bash
# Health check
curl http://localhost:7860/api/health

# Translate a PDF (default backend, default output = translated PDF)
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
