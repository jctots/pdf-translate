# Paperless-ngx Integration

Automatically translate documents as they are ingested into [Paperless-ngx](https://docs.paperless-ngx.com/). A companion translated PDF is uploaded alongside the original and linked via custom fields — fully local, no cloud.

## How it works

A lightweight webhook container runs alongside Paperless. When Paperless ingests a new document, its Workflow engine calls the webhook. The receiver detects the document language, sends the PDF to [pdf-translate](../README.md), and uploads the translated companion back to Paperless. The original is never modified.

```
Paperless ingests document → OCR completes
        ↓
Paperless Workflow fires POST /webhook
        ↓
  detect language from OCR content (LibreTranslate /detect)
  ├── not <source-lang> → skip (logged)
  └── <source-lang> → download PDF from Paperless
                    → POST to pdf-translate
                    → upload translated PDF to Paperless
                    → link both docs via Document Link custom fields
                    → log result
```

The webhook responds 200 immediately — translation runs in the background and never blocks document consumption.

## Prerequisites

- Paperless-ngx v2.x
- [pdf-translate](../README.md) running and reachable
- LibreTranslate running and reachable (used for language detection)

## One-time setup in Paperless

### 1. Create custom fields

Go to **Settings → Custom Fields** and create two fields:

| Name | Data type |
|---|---|
| `has_translation` | Document Link |
| `translation_of` | Document Link |

**Document Link** fields render as clickable links in the Paperless UI — click `has_translation` on the original to jump to its translation, and `translation_of` on the translation to return to the original.

The webhook logs a warning if these fields are missing but still uploads the translation — the document just won't be linked.

### 2. Generate a Paperless API token

Go to **My Profile → API Token** and copy the token. Store it in your `.env` file as `PAPERLESS_API_TOKEN`.

### 3. Configure a Paperless Workflow

Go to **Settings → Workflows → Add Workflow**:

| Setting | Value |
|---|---|
| Name | Auto-translate |
| Trigger | Document added |
| Filter | *(leave empty to process all documents)* |
| Action | Webhook |
| URL | `http://pdf-translate-webhook:8081/webhook` |

The URL uses the Docker service name — both containers must be on the same Docker network (the default compose network satisfies this automatically).

## Deploy

Add the webhook service alongside your existing Paperless services in `docker-compose.yml`. No changes to the `paperless-ngx` service are needed.

```yaml
services:
  # ... your existing paperless-ngx, db, broker, etc. ...

  pdf-translate-webhook:
    image: ghcr.io/jctots/pdf-translate-paperless-webhook:latest
    container_name: pdf-translate-webhook
    ports:
      - "8081:8081"
    volumes:
      - <data-path>/pdf-translate-webhook:/data
    environment:
      # Paperless connection
      PAPERLESS_URL: http://paperless-ngx:8000
      PAPERLESS_API_TOKEN: ${PAPERLESS_API_TOKEN}        # in .env (secret)

      # Translation pipeline
      PDF_TRANSLATE_URL: http://<pdf-translate-host>:7860
      TRANSLATE_SOURCE_LANG: de                          # ISO 639-1, e.g. de, fr, nl — or "auto"
      TRANSLATE_TARGET_LANG: en
      TRANSLATE_OUTPUT: pdf                              # pdf | sbs | both
      TRANSLATE_TIMEOUT: "300"
      TRANSLATE_LOG_FILE: /data/translate.log

      # pdf-translate API settings — language detection + translation backend
      LIBRETRANSLATE_URL: http://<libretranslate-host>:5000
      PDF_TRANSLATE_SERVICE: LibreTranslate              # LibreTranslate | Ollama | Google
      # PDF_TRANSLATE_LIBRE_URL: http://<libretranslate-host>:5000  # defaults to LIBRETRANSLATE_URL
      # PDF_TRANSLATE_LIBRE_KEY: ""                      # if your LT instance requires an API key

      # pdf-translate API settings — processing flags
      PDF_TRANSLATE_MERGE_BLOCKS: "true"                 # recommended: merge fragmented text (DTP/Paperless-archived PDFs)
      # PDF_TRANSLATE_FORCE_OCR: "false"                 # force OCR even when text layer present
      # PDF_TRANSLATE_ALLOW_WRAP: "false"                # collapse hard line breaks before translation
      # PDF_TRANSLATE_FILTER_ICONS: "true"               # strip single-character icon glyphs
      # PDF_TRANSLATE_DETECT_TABLES: "true"              # shrink-to-fit for table cells
    restart: unless-stopped
    depends_on:
      - paperless-ngx
```

```bash
docker compose up -d
```

## Environment variables

### Paperless connection

| Variable | Default | Description |
|---|---|---|
| `PAPERLESS_URL` | `http://paperless-ngx:8000` | Paperless base URL — use the Docker service name when on the same compose network |
| `PAPERLESS_API_TOKEN` | — | Paperless API token — keep in `.env` (secret) |

### Translation pipeline

| Variable | Default | Description |
|---|---|---|
| `PDF_TRANSLATE_URL` | `http://localhost:7860` | pdf-translate base URL |
| `TRANSLATE_SOURCE_LANG` | `auto` | Source language (ISO 639-1, e.g. `de`, `fr`, `nl`). Use `auto` to translate every document regardless of detected language. |
| `TRANSLATE_TARGET_LANG` | `en` | Target language (ISO 639-1) |
| `TRANSLATE_OUTPUT` | `pdf` | What to upload: `pdf` (translated PDF), `sbs` (side-by-side bilingual PDF), or `both` (one of each — two companions per original) |
| `TRANSLATE_TIMEOUT` | `300` | Seconds to wait for pdf-translate. The orphaned job is cancelled automatically on timeout. |
| `TRANSLATE_LOG_FILE` | `/data/translate.log` | Append-only JSON log. Mount `/data` to persist it on the host. |

### pdf-translate API settings

These are passed as request parameters on every call to pdf-translate. They configure how pdf-translate processes and translates the PDF. The pdf-translate REST API never reads its own `config.json` — all settings must come from the caller.

| Variable | Default | Description |
|---|---|---|
| `LIBRETRANSLATE_URL` | `http://localhost:5000` | LibreTranslate base URL — used for **language detection** by this container |
| `PDF_TRANSLATE_SERVICE` | `LibreTranslate` | Translation backend passed to pdf-translate: `LibreTranslate`, `Ollama`, or `Google` |
| `PDF_TRANSLATE_LIBRE_URL` | *(= `LIBRETRANSLATE_URL`)* | LibreTranslate URL passed to pdf-translate for **translation**. Defaults to `LIBRETRANSLATE_URL`. Set separately if pdf-translate reaches LT via a different network path. |
| `PDF_TRANSLATE_LIBRE_KEY` | *(empty)* | LibreTranslate API key (if your instance requires one) |
| `PDF_TRANSLATE_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL (only relevant when `PDF_TRANSLATE_SERVICE=Ollama`) |
| `PDF_TRANSLATE_OLLAMA_MODEL` | *(pdf-translate default)* | Ollama translation model name |
| `PDF_TRANSLATE_OLLAMA_KEY` | *(empty)* | Ollama API key (optional) |
| `PDF_TRANSLATE_MERGE_BLOCKS` | `false` | Merge split word-level text fragments before translation. **Enable for DTP/InDesign PDFs** or Paperless-archived PDFs with fragmented text. |
| `PDF_TRANSLATE_FORCE_OCR` | `false` | Ignore the text layer and OCR every page. Use for scanned PDFs with no text layer. |
| `PDF_TRANSLATE_ALLOW_WRAP` | `false` | Collapse line breaks before translation (helps with hard-wrapped paragraphs) |
| `PDF_TRANSLATE_FILTER_ICONS` | `true` | Strip single-character icon glyphs from mixed text blocks |
| `PDF_TRANSLATE_DETECT_TABLES` | `true` | Detect table cells and apply shrink-to-fit text fitting |

## Output format

`TRANSLATE_OUTPUT` controls what is uploaded to Paperless alongside the original:

| Value | Uploaded | Paperless indexes | Notes |
|---|---|---|---|
| `pdf` (default) | Translated PDF | Target language text | Portrait, clean, readable |
| `sbs` | Side-by-side PDF | Both source and target language | Landscape; both languages visible |
| `both` | Translated PDF + SBS PDF | Both | Two companion documents; pdf-translate called twice |

## Log format

One JSON line per document, appended to `TRANSLATE_LOG_FILE`.

```jsonc
// Successful translation
{"action": "translated", "source_id": 142, "source_title": "Document title", "uploaded": [{"fmt": "pdf", "id": 201, "title": "[EN] Document title"}], "ts": "2026-05-27T10:03:00Z"}

// Skipped — not the configured source language
{"action": "skipped", "source_id": 143, "source_title": "Document title", "reason": "lang=en", "ts": "..."}

// Skipped — already translated (idempotency guard)
{"action": "skipped", "source_id": 142, "source_title": "Document title", "reason": "already translated", "ts": "..."}

// Timed out
{"action": "timeout", "source_id": 144, "source_title": "Document title", "reason": "exceeded 300s", "ts": "..."}
```

To review failures: `grep '"action": "failed"\|"action": "timeout"' /data/translate.log`

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/webhook` | Paperless-ngx document webhook receiver |

## Troubleshooting

**Translation triggered for documents in the wrong language**
Language detection failed (LibreTranslate unreachable) or `TRANSLATE_SOURCE_LANG=auto`. Check `LIBRETRANSLATE_URL` and that the service is running.

**Custom fields not set after translation**
The `has_translation` / `translation_of` fields were not found in Paperless. Complete the one-time setup above. To link an already-translated document manually: set the custom fields in the Paperless UI using the IDs from the log (`source_id` and `translation_id`).

**Webhook not firing**
Verify the Paperless Workflow configuration. The webhook URL must be reachable from inside the Paperless container — use the Docker service name (`http://pdf-translate-webhook:8081/webhook`), not `localhost`.

**Timeout on large documents**
Increase `TRANSLATE_TIMEOUT`. Check `GET <pdf-translate-url>/api/health` to see if a job is still running or was cancelled.

**pdf-translate queue full (HTTP 429)**
Another translation is already running or queued. The webhook logs the failure. The document will not be retried automatically — re-trigger the Paperless Workflow for that document after the queue clears.
