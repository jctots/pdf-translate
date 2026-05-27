"""
webhook.py — Paperless-ngx webhook receiver for pdf-translate

Paperless-ngx v2 Workflow triggers POST /webhook when a document is added.
The handler detects language, translates via pdf-translate, and uploads a
companion translated PDF back to Paperless. The original is never modified.

Deploy:
    docker run -p 8081:8081 \\
      -e PAPERLESS_URL=http://paperless-ngx:8000 \\
      -e PAPERLESS_API_TOKEN=<token> \\
      -e PDF_TRANSLATE_URL=http://<host>:7860 \\
      ghcr.io/jctots/pdf-translate-paperless-webhook:latest

See README.md for full configuration and Paperless Workflow setup.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("paperless-webhook")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PAPERLESS_URL   = os.environ.get("PAPERLESS_URL",   "http://paperless-ngx:8000").rstrip("/")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_API_TOKEN", "")

PDF_TRANSLATE_URL  = os.environ.get("PDF_TRANSLATE_URL",  "http://localhost:7860").rstrip("/")
LIBRETRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "http://localhost:5000").rstrip("/")

SOURCE_LANG   = os.environ.get("TRANSLATE_SOURCE_LANG", "auto")  # ISO 639-1 or "auto"
TARGET_LANG   = os.environ.get("TRANSLATE_TARGET_LANG", "en")
OUTPUT_FORMAT = os.environ.get("TRANSLATE_OUTPUT",      "pdf")   # pdf | sbs | both
TIMEOUT       = float(os.environ.get("TRANSLATE_TIMEOUT",  "300"))
LOG_FILE      = os.environ.get("TRANSLATE_LOG_FILE",  "/data/translate.log")

# ---------------------------------------------------------------------------
# pdf-translate API settings — passed as request parameters on every call.
# These configure how pdf-translate processes and translates the PDF.
# pdf-translate never reads config.json for API calls; all settings come from
# the request. Set these env vars on the webhook container to tune behavior.
# ---------------------------------------------------------------------------

API_SERVICE      = os.environ.get("PDF_TRANSLATE_SERVICE",       "LibreTranslate")
# URL passed to pdf-translate for translation calls (may differ from
# LIBRETRANSLATE_URL if the LT instance is on a different network path from
# the pdf-translate container). Defaults to LIBRETRANSLATE_URL.
API_LIBRE_URL    = os.environ.get("PDF_TRANSLATE_LIBRE_URL",     LIBRETRANSLATE_URL)
API_LIBRE_KEY    = os.environ.get("PDF_TRANSLATE_LIBRE_KEY",     "")
API_OLLAMA_URL   = os.environ.get("PDF_TRANSLATE_OLLAMA_URL",    "http://localhost:11434")
API_OLLAMA_MODEL = os.environ.get("PDF_TRANSLATE_OLLAMA_MODEL",  "")
API_OLLAMA_KEY   = os.environ.get("PDF_TRANSLATE_OLLAMA_KEY",    "")

def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name, "")
    return default if v == "" else v.lower() in ("1", "true", "yes")

API_MERGE_BLOCKS  = _bool_env("PDF_TRANSLATE_MERGE_BLOCKS",  False)
API_FORCE_OCR     = _bool_env("PDF_TRANSLATE_FORCE_OCR",     False)
API_ALLOW_WRAP    = _bool_env("PDF_TRANSLATE_ALLOW_WRAP",    False)
API_FILTER_ICONS  = _bool_env("PDF_TRANSLATE_FILTER_ICONS",  True)
API_DETECT_TABLES = _bool_env("PDF_TRANSLATE_DETECT_TABLES", True)

PAPERLESS_HEADERS = {"Authorization": f"Token {PAPERLESS_TOKEN}"}

FIELD_HAS_TRANSLATION = "has_translation"
FIELD_TRANSLATION_OF  = "translation_of"
TAG_AUTO_TRANSLATED   = "auto-translated"

# ---------------------------------------------------------------------------
# Structured log
# ---------------------------------------------------------------------------

def emit(entry: dict) -> None:
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(entry, ensure_ascii=False)
    logger.info(line)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            logger.warning("could not write to %s: %s", LOG_FILE, exc)

# ---------------------------------------------------------------------------
# Paperless API helpers
# ---------------------------------------------------------------------------

def _pl_get(client: httpx.Client, path: str) -> dict:
    r = client.get(f"{PAPERLESS_URL}{path}", headers=PAPERLESS_HEADERS, timeout=15.0)
    r.raise_for_status()
    return r.json()


def get_document(client: httpx.Client, doc_id: int) -> dict:
    return _pl_get(client, f"/api/documents/{doc_id}/")


def download_pdf(client: httpx.Client, doc_id: int) -> bytes:
    r = client.get(
        f"{PAPERLESS_URL}/api/documents/{doc_id}/download/",
        headers=PAPERLESS_HEADERS,
        timeout=60.0,
    )
    r.raise_for_status()
    return r.content


def get_custom_field_ids(client: httpx.Client) -> dict[str, int]:
    data = _pl_get(client, "/api/custom_fields/?page_size=100")
    return {
        f["name"]: f["id"]
        for f in data.get("results", [])
        if f["name"] in (FIELD_HAS_TRANSLATION, FIELD_TRANSLATION_OF)
    }


def get_tag_id_by_name(client: httpx.Client, name: str) -> int | None:
    try:
        data = _pl_get(client, f"/api/tags/?name={name}")
        results = data.get("results", [])
        if results:
            return results[0]["id"]
    except Exception:
        pass
    return None


def get_or_create_tag(client: httpx.Client, name: str) -> int:
    data = _pl_get(client, f"/api/tags/?name={name}")
    results = data.get("results", [])
    if results:
        return results[0]["id"]
    r = client.post(
        f"{PAPERLESS_URL}/api/tags/",
        headers=PAPERLESS_HEADERS,
        json={"name": name},
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()["id"]


def upload_document(client: httpx.Client, pdf_bytes: bytes, title: str, tag_id: int) -> str:
    """Upload PDF to Paperless. Returns the task UUID."""
    r = client.post(
        f"{PAPERLESS_URL}/api/documents/post_document/",
        headers=PAPERLESS_HEADERS,
        files={"document": (f"{title}.pdf", pdf_bytes, "application/pdf")},
        data={"title": title, "tags": [tag_id]},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.text.strip().strip('"')


def poll_task(client: httpx.Client, task_uuid: str, max_wait: int = 120) -> int | None:
    """Poll until task completes. Returns new document ID or None."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(3)
        try:
            data = _pl_get(client, f"/api/tasks/?task_id={task_uuid}")
            tasks = data if isinstance(data, list) else data.get("results", [])
            if tasks:
                task = tasks[0]
                if task.get("status") == "SUCCESS":
                    doc_id = task.get("related_document")
                    return int(doc_id) if doc_id else None
                if task.get("status") in ("FAILURE", "REVOKED"):
                    return None
        except Exception:
            pass
    return None


def patch_custom_field(
    client: httpx.Client, doc_id: int, field_id: int, value: int, existing: list
) -> None:
    fields = [f for f in existing if f.get("field") != field_id]
    fields.append({"field": field_id, "value": value})
    r = client.patch(
        f"{PAPERLESS_URL}/api/documents/{doc_id}/",
        headers=PAPERLESS_HEADERS,
        json={"custom_fields": fields},
        timeout=15.0,
    )
    r.raise_for_status()

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str | None:
    """Returns ISO 639-1 code or None on failure."""
    if not text:
        return None
    try:
        r = httpx.post(
            f"{LIBRETRANSLATE_URL}/detect",
            json={"q": text[:1000]},
            timeout=10.0,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            return results[0]["language"]
    except Exception as exc:
        logger.warning("language detection failed: %s", exc)
    return None

# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def translate_pdf(pdf_bytes: bytes, fmt: str, filename: str) -> bytes:
    data: dict = {
        "source":        SOURCE_LANG,
        "target":        TARGET_LANG,
        "outputs":       fmt,
        "service":       API_SERVICE,
        "libre_url":     API_LIBRE_URL,
        "ollama_url":    API_OLLAMA_URL,
        "merge_blocks":  str(API_MERGE_BLOCKS).lower(),
        "force_ocr":     str(API_FORCE_OCR).lower(),
        "allow_wrap":    str(API_ALLOW_WRAP).lower(),
        "filter_icons":  str(API_FILTER_ICONS).lower(),
        "detect_tables": str(API_DETECT_TABLES).lower(),
    }
    if API_LIBRE_KEY:
        data["libre_key"] = API_LIBRE_KEY
    if API_OLLAMA_MODEL:
        data["ollama_model"] = API_OLLAMA_MODEL
    if API_OLLAMA_KEY:
        data["ollama_key"] = API_OLLAMA_KEY
    r = httpx.post(
        f"{PDF_TRANSLATE_URL}/api/translate",
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data=data,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.content

# ---------------------------------------------------------------------------
# Core handler — runs in FastAPI background task (thread pool)
# ---------------------------------------------------------------------------

def handle(doc_id: int, content: str | None) -> None:
    with httpx.Client() as client:

        # 1. Fetch full document metadata
        try:
            doc = get_document(client, doc_id)
        except Exception as exc:
            emit({"action": "error", "source_id": doc_id, "reason": f"fetch failed: {exc}"})
            return

        title   = doc.get("title", "Untitled")
        content = content or doc.get("content", "")  # payload content saves one API call

        # 2. Idempotency — skip if this document is itself a translated companion.
        #    Companion documents are tagged "auto-translated" at upload time, so this
        #    check is race-condition-free: the tag is present before Paperless OCR
        #    completes and the Workflow fires.
        auto_tag_id = get_tag_id_by_name(client, TAG_AUTO_TRANSLATED)
        if auto_tag_id is not None and auto_tag_id in doc.get("tags", []):
            emit({"action": "skipped", "source_id": doc_id, "source_title": title,
                  "reason": "auto-translated companion"})
            return

        # 3. Language detection — skip when SOURCE_LANG is not "auto"
        if SOURCE_LANG != "auto":
            detected = detect_language(content)
            if detected is not None and detected != SOURCE_LANG:
                emit({"action": "skipped", "source_id": doc_id, "source_title": title,
                      "reason": f"lang={detected}"})
                return

        # 4. Custom field IDs
        try:
            field_ids = get_custom_field_ids(client)
        except Exception as exc:
            emit({"action": "error", "source_id": doc_id, "source_title": title,
                  "reason": f"field lookup failed: {exc}"})
            return

        # 5. Idempotency guard — skip original if it already has a translation linked
        has_translation_field_id = field_ids.get(FIELD_HAS_TRANSLATION)
        if has_translation_field_id and any(
            f.get("field") == has_translation_field_id
            for f in doc.get("custom_fields", [])
        ):
            emit({"action": "skipped", "source_id": doc_id, "source_title": title,
                  "reason": "already translated"})
            return

        # 6. Download original PDF
        try:
            pdf_bytes = download_pdf(client, doc_id)
        except Exception as exc:
            emit({"action": "failed", "source_id": doc_id, "source_title": title,
                  "reason": f"download failed: {exc}"})
            return

        # 7. Get or create the auto-translated tag
        try:
            tag_id = get_or_create_tag(client, TAG_AUTO_TRANSLATED)
        except Exception as exc:
            emit({"action": "failed", "source_id": doc_id, "source_title": title,
                  "reason": f"tag lookup failed: {exc}"})
            return

        # 8. Translate and upload each format
        formats = ["pdf", "sbs"] if OUTPUT_FORMAT == "both" else [OUTPUT_FORMAT]
        translation_id: int | None = None
        uploaded: list[dict] = []

        for fmt in formats:
            label = {
                "pdf": f"[{TARGET_LANG.upper()}]",
                "sbs": f"[{SOURCE_LANG.upper()}↔{TARGET_LANG.upper()}]",
            }[fmt]
            companion_title = f"{label} {title}"

            try:
                out_bytes = translate_pdf(pdf_bytes, fmt, f"{title}.pdf")
            except httpx.TimeoutException:
                emit({"action": "timeout", "source_id": doc_id, "source_title": title,
                      "format": fmt, "reason": f"exceeded {int(TIMEOUT)}s"})
                try:
                    client.delete(f"{PDF_TRANSLATE_URL}/api/translate", timeout=10.0)
                except Exception:
                    pass
                return
            except httpx.HTTPStatusError as exc:
                emit({"action": "failed", "source_id": doc_id, "source_title": title,
                      "format": fmt,
                      "reason": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"})
                return
            except Exception as exc:
                emit({"action": "failed", "source_id": doc_id, "source_title": title,
                      "format": fmt, "reason": str(exc)})
                return

            try:
                task_uuid = upload_document(client, out_bytes, companion_title, tag_id)
            except Exception as exc:
                emit({"action": "failed", "source_id": doc_id, "source_title": title,
                      "format": fmt, "reason": f"upload failed: {exc}"})
                return

            companion_id = poll_task(client, task_uuid)
            if not companion_id:
                emit({"action": "uploaded_unlinked", "source_id": doc_id,
                      "source_title": title, "format": fmt, "task_uuid": task_uuid,
                      "reason": "could not resolve companion document ID from task"})
                return

            uploaded.append({"fmt": fmt, "id": companion_id, "title": companion_title})
            if translation_id is None:
                translation_id = companion_id

        # 9. Bidirectional custom field links
        errors = []
        translation_of_field_id = field_ids.get(FIELD_TRANSLATION_OF)

        if has_translation_field_id and translation_id:
            try:
                patch_custom_field(
                    client, doc_id, has_translation_field_id,
                    translation_id, doc.get("custom_fields", [])
                )
            except Exception as exc:
                errors.append(f"patch source: {exc}")
        elif not has_translation_field_id:
            errors.append(
                f"custom field '{FIELD_HAS_TRANSLATION}' not found — see README.md § One-time setup"
            )

        if translation_of_field_id and translation_id:
            try:
                patch_custom_field(client, translation_id, translation_of_field_id, doc_id, [])
            except Exception as exc:
                errors.append(f"patch translation: {exc}")
        elif not translation_of_field_id:
            errors.append(
                f"custom field '{FIELD_TRANSLATION_OF}' not found — see README.md § One-time setup"
            )

        emit({
            "action": "translated",
            "source_id": doc_id,
            "source_title": title,
            "uploaded": uploaded,
            **({"errors": errors} if errors else {}),
        })

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="pdf-translate — Paperless-ngx webhook",
    description="Receives Paperless-ngx workflow webhooks and auto-translates documents via pdf-translate.",
)


@app.get("/health", summary="Health check")
def health():
    return {"status": "ok"}


@app.post("/webhook", summary="Paperless-ngx document webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive a Paperless-ngx workflow webhook (document added trigger).

    Responds 200 immediately. Translation runs in the background — check
    TRANSLATE_LOG_FILE for results.
    """
    raw = await request.body()

    # Log full request for payload format discovery
    logger.info(
        "webhook: method=%s headers=%s query=%s body=%s",
        request.method,
        dict(request.headers),
        dict(request.query_params),
        raw[:1000],
    )

    payload: dict = {}
    if raw:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                logger.error("webhook: unexpected payload type %s", type(payload))
                payload = {}
        except Exception as exc:
            logger.error("webhook: failed to parse JSON: %s", exc)

    # Try body fields, then query params, then parse from doc_url
    doc_id = (
        payload.get("id")
        or payload.get("document_id")
        or request.query_params.get("id")
        or request.query_params.get("document_id")
    )
    if not doc_id:
        doc_url = payload.get("doc_url", "") or request.query_params.get("doc_url", "")
        if doc_url:
            m = re.search(r"/documents/(\d+)/", doc_url)
            if m:
                doc_id = m.group(1)
    if not doc_id:
        logger.warning("webhook: no document id found | payload keys: %s | query: %s",
                       list(payload.keys()), dict(request.query_params))
        return JSONResponse({"status": "ignored", "reason": "no document id in payload"})

    # Use content from payload if present — saves one Paperless API call
    content = payload.get("content")

    background_tasks.add_task(handle, int(doc_id), content)
    return {"status": "accepted", "document_id": doc_id}
