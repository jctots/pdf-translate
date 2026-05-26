"""
pdf-translate — Gradio UI.
Run: python app.py
"""

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import fitz  # PyMuPDF
import gradio as gr
import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from backends import test_libre_connection, test_ollama_connection, translate, translate_sync
from config import (
    CONFIG_PATH,
    OCR_DEFAULT_SERVICE,
    OCR_LLM_DEFAULT_MODEL,
    OCR_LLM_DEFAULT_PROMPT,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    load_config,
    save_config,
    update_config,
)
from pdf_utils import pdf_to_image

# ---------------------------------------------------------------------------
# Page navigation helpers
# ---------------------------------------------------------------------------


def _page_count(pdf_path: str | None) -> int:
    if not pdf_path:
        return 1
    try:
        doc = fitz.open(pdf_path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 1


def on_pdf_upload(pdf_path: str | None):
    """Show first page of uploaded PDF; clear translated preview, state, and nav buttons."""
    hide = gr.update(visible=False)
    if not pdf_path:
        return None, None, 0, "—", hide, hide
    n = _page_count(pdf_path)
    img = pdf_to_image(pdf_path, 0)
    return img, None, 0, f"Page 1 / {n}", hide, hide


def go_prev(orig_path: str | None, trans_path: str | None, page_idx: int):
    if not orig_path:
        return gr.update(), gr.update(), page_idx, "—"
    n = _page_count(orig_path)
    new_idx = (page_idx - 1) % n  # wraps: page 1 → last page
    orig_img = pdf_to_image(orig_path, new_idx)
    trans_update = pdf_to_image(trans_path, new_idx) if trans_path else gr.update()
    return orig_img, trans_update, new_idx, f"Page {new_idx + 1} / {n}"


def go_next(orig_path: str | None, trans_path: str | None, page_idx: int):
    if not orig_path:
        return gr.update(), gr.update(), page_idx, "—"
    n = _page_count(orig_path)
    new_idx = (page_idx + 1) % n  # wraps: last page → page 1
    orig_img = pdf_to_image(orig_path, new_idx)
    trans_update = pdf_to_image(trans_path, new_idx) if trans_path else gr.update()
    return orig_img, trans_update, new_idx, f"Page {new_idx + 1} / {n}"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _build_version() -> str:
    """Return a version string.

    Priority:
    1. ``git describe --tags --always --dirty`` — dev runs with a git checkout.
       Produces ``v0.1.0`` on a tagged release, ``v0.1.0-5-g68f2e06`` on a dev
       build, ``g68f2e06-dirty`` when the working tree has uncommitted changes.
    2. ``VERSION`` file — written by CI (``git describe --tags --always``) before
       the Docker image is built.  Always clean (no ``-dirty`` suffix).
    3. ``"unknown"`` — fallback when neither is available.
    """
    try:
        label = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=Path(__file__).parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if label:
            return label
    except Exception:
        pass
    # Fallback: VERSION file written by CI before docker build
    try:
        return (Path(__file__).parent / "VERSION").read_text().strip()
    except Exception:
        return "unknown"


_DEBUG = os.environ.get("PDF_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")

cfg = load_config()
_VERSION = _build_version()

# Startup log — always visible in docker logs; helps diagnose config-path issues
_config_status = "found" if CONFIG_PATH.exists() else "not found"
print(f"[pdf-translate] version={_VERSION} config={CONFIG_PATH} ({_config_status}) debug={_DEBUG}", flush=True)

_ICON_SVG = (
    (Path(__file__).parent / "icon.svg")
    .read_text(encoding="utf-8")
    .replace('height="48px"', 'height="36px"')
    .replace('width="48px"', 'width="36px"')
)

with gr.Blocks(title="PDF Translate") as gradio_app:
    gr.HTML(
        f"""<div style="display:flex;align-items:center;gap:0.5em;margin-bottom:0.25em;">
  {_ICON_SVG}
  <h1 style="margin:0;font-size:1.8em;line-height:1;">PDF Translate
    <span style="font-size:0.45em;font-weight:normal;vertical-align:middle;margin-left:0.6em;">build: <code>{_VERSION}</code></span>
  </h1>
</div>"""
    )

    # Persistent state
    page_state           = gr.State(0)     # current page index (0-based)
    translated_path_state = gr.State(None)  # path to translated PDF (for navigation)

    with gr.Row():

        # ------------------------------------------------------------------
        # Column 1 — controls
        # ------------------------------------------------------------------
        with gr.Column(scale=1):
            pdf_input = gr.File(
                label="PDF file",
                file_types=[".pdf"],
                type="filepath",
            )
            source_lang = gr.Dropdown(
                label="Source language",
                choices=[(label, code) for label, code in SOURCE_LANGUAGES],
                value=cfg["source"],
            )
            target_lang = gr.Dropdown(
                label="Target language",
                choices=[(label, code) for label, code in TARGET_LANGUAGES],
                value=cfg["target"],
            )
            backend = gr.Dropdown(
                label="Translation service",
                choices=["Ollama", "LibreTranslate", "Google"],
                value=cfg["backend"],
            )
            translate_btn = gr.Button("Translate", variant="primary", size="lg")
            with gr.Accordion("Advanced settings", open=False):
                force_ocr_cb = gr.Checkbox(
                    label="Force OCR (ignore text layer)",
                    value=cfg.get("force_ocr", False),
                )
                ocr_service = gr.Dropdown(
                    label="OCR engine (scanned PDFs only)",
                    choices=["Tesseract", "Ollama"],
                    value=cfg.get("ocr_service", OCR_DEFAULT_SERVICE),
                )
                allow_wrap_cb = gr.Checkbox(
                    label="Allow text reflow (collapse line breaks)",
                    value=cfg["allow_wrap"],
                )
                filter_icons_cb = gr.Checkbox(
                    label="Filter icon/symbol glyphs",
                    value=cfg["filter_icons"],
                )
                merge_blocks_cb = gr.Checkbox(
                    label="Merge split lines (DTP/InDesign PDFs)",
                    value=cfg.get("merge_blocks", False),
                )
                detect_tables_cb = gr.Checkbox(
                    label="Detect table cells (shrink-to-fit)",
                    value=cfg.get("detect_tables", True),
                )
            status_box = gr.Textbox(
                label="Status",
                interactive=False,
                lines=3,
                placeholder="Status will appear here after translation starts…",
            )
            download_translated = gr.File(label="Translated PDF", visible=False)
            download_sbs  = gr.File(label="Side-by-side PDF (original | translation)", visible=False)
            download_html = gr.File(label="Side-by-side HTML (original | translation)", visible=False)

            with gr.Accordion("Backend settings", open=False):
                with gr.Group():
                    gr.Markdown("**Ollama**")
                    ollama_url = gr.Textbox(label="URL (shared for translation and OCR)", value=cfg["ollama_url"])
                    ollama_key = gr.Textbox(
                        label="API key (optional)",
                        value=cfg["ollama_key"],
                        type="password",
                    )
                    ollama_test_btn    = gr.Button("Test connection", size="sm")
                    ollama_test_result = gr.Markdown("")
                    ollama_model = gr.Textbox(label="Translation model", value=cfg["ollama_model"])
                    with gr.Accordion("Translation system prompt", open=False):
                        ollama_system_prompt = gr.Textbox(
                            label="",
                            value=cfg["ollama_system_prompt"],
                            lines=8,
                            max_lines=20,
                        )
                    ocr_ollama_model = gr.Textbox(
                        label="OCR model",
                        value=cfg.get("ocr_ollama_model", OCR_LLM_DEFAULT_MODEL),
                    )
                    with gr.Accordion("OCR prompt", open=False):
                        ocr_ollama_prompt = gr.Textbox(
                            label="",
                            value=cfg.get("ocr_ollama_prompt", OCR_LLM_DEFAULT_PROMPT),
                            lines=4,
                            max_lines=10,
                        )

                with gr.Group():
                    gr.Markdown("**LibreTranslate**")
                    libre_url = gr.Textbox(label="URL", value=cfg["libre_url"])
                    libre_key = gr.Textbox(
                        label="API key (optional)",
                        value=cfg["libre_key"],
                        type="password",
                    )
                    libre_test_btn    = gr.Button("Test connection", size="sm")
                    libre_test_result = gr.Markdown("")

                with gr.Row():
                    save_btn    = gr.Button("Save configuration", size="sm")
                    save_status = gr.Markdown("")

        # ------------------------------------------------------------------
        # Column 2 — original preview + prev navigation
        # ------------------------------------------------------------------
        with gr.Column(scale=2):
            orig_preview = gr.Image(
                label="Original",
                type="filepath",
                interactive=False,
            )
            prev_btn       = gr.Button("◀ Prev", size="sm", visible=False)
            page_indicator = gr.Textbox(visible=False, value="—")

        # ------------------------------------------------------------------
        # Column 3 — translated preview + next navigation
        # ------------------------------------------------------------------
        with gr.Column(scale=2):
            trans_preview = gr.Image(
                label="Translated",
                type="filepath",
                interactive=False,
            )
            next_btn = gr.Button("Next ▶", size="sm", visible=False)

    # --- Event wiring -----------------------------------------------------

    # Re-read config.json on every page load so saved settings survive a reload
    def _refresh_config():
        c = load_config()
        force_ocr = c.get("force_ocr", False)
        return (
            c["source"],
            c["target"],
            c["backend"],
            c["allow_wrap"],
            # Text-layer-only options: disable when force_ocr is saved as True
            gr.update(value=c["filter_icons"],           interactive=not force_ocr),
            gr.update(value=c.get("merge_blocks", False), interactive=not force_ocr),
            gr.update(value=c.get("detect_tables", True), interactive=not force_ocr),
            force_ocr,
            c["ollama_url"],
            c["ollama_model"],
            c["ollama_system_prompt"],
            c["ollama_key"],
            c["libre_url"],
            c["libre_key"],
            c.get("ocr_service", OCR_DEFAULT_SERVICE),
            c.get("ocr_ollama_model", OCR_LLM_DEFAULT_MODEL),
            c.get("ocr_ollama_prompt", OCR_LLM_DEFAULT_PROMPT),
        )

    gradio_app.load(
        _refresh_config,
        outputs=[source_lang, target_lang, backend, allow_wrap_cb, filter_icons_cb,
                 merge_blocks_cb, detect_tables_cb, force_ocr_cb,
                 ollama_url, ollama_model, ollama_system_prompt, ollama_key, libre_url, libre_key,
                 ocr_service, ocr_ollama_model, ocr_ollama_prompt],
    )

    # Disable text-layer-only options when Force OCR is checked — they have no
    # effect when the text layer is bypassed entirely.
    # allow_wrap_cb is kept interactive because line-break collapsing applies to
    # OCR paragraphs too.
    def _toggle_text_layer_opts(force_ocr: bool):
        return (
            gr.update(interactive=not force_ocr),  # filter_icons_cb
            gr.update(interactive=not force_ocr),  # merge_blocks_cb
            gr.update(interactive=not force_ocr),  # detect_tables_cb
        )

    force_ocr_cb.change(
        _toggle_text_layer_opts,
        inputs=[force_ocr_cb],
        outputs=[filter_icons_cb, merge_blocks_cb, detect_tables_cb],
    )

    # Show first page on upload; clear translated state; hide nav buttons
    pdf_input.change(
        on_pdf_upload,
        inputs=[pdf_input],
        outputs=[orig_preview, trans_preview, page_state, page_indicator, prev_btn, next_btn],
    )

    # Page navigation
    prev_btn.click(
        go_prev,
        inputs=[pdf_input, translated_path_state, page_state],
        outputs=[orig_preview, trans_preview, page_state, page_indicator],
    )
    next_btn.click(
        go_next,
        inputs=[pdf_input, translated_path_state, page_state],
        outputs=[orig_preview, trans_preview, page_state, page_indicator],
    )

    # Backend connection tests
    ollama_test_btn.click(
        test_ollama_connection,
        inputs=[ollama_url, ollama_key],
        outputs=[ollama_test_result],
    )
    libre_test_btn.click(
        test_libre_connection,
        inputs=[libre_url],
        outputs=[libre_test_result],
    )

    # Save config
    save_btn.click(
        save_config,
        inputs=[
            backend,
            source_lang,
            target_lang,
            allow_wrap_cb,
            filter_icons_cb,
            ollama_url,
            ollama_model,
            ollama_system_prompt,
            ollama_key,
            libre_url,
            libre_key,
            ocr_service,
            ocr_ollama_model,
            ocr_ollama_prompt,
            merge_blocks_cb,
            detect_tables_cb,
            force_ocr_cb,
        ],
        outputs=[save_status],
    )

    # Translate — outputs match the 11-tuple yielded by translate()
    translate_btn.click(
        translate,
        inputs=[
            pdf_input,
            source_lang,
            target_lang,
            backend,
            ollama_url,
            ollama_model,
            ollama_system_prompt,
            ollama_key,
            libre_url,
            libre_key,
            allow_wrap_cb,
            filter_icons_cb,
            ocr_service,
            ocr_ollama_model,
            ocr_ollama_prompt,
            merge_blocks_cb,
            detect_tables_cb,
            force_ocr_cb,
        ],
        outputs=[
            download_translated,
            download_sbs,
            download_html,
            orig_preview,
            trans_preview,
            status_box,
            page_state,
            page_indicator,
            translated_path_state,
            prev_btn,
            next_btn,
        ],
    )


# ---------------------------------------------------------------------------
# Job state (in-process — single-user PoC)
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    """Tracks the current and last-completed translation job."""
    # Current job
    running: bool = False
    queued: int = 0                             # requests waiting for the semaphore
    started_at: str | None = None
    source: str | None = None
    target: str | None = None
    backend: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # Last result — persists until the next job starts
    last_status: str | None = None              # "completed" | "failed" | "cancelled"
    last_started_at: str | None = None
    last_completed_at: str | None = None
    last_source: str | None = None
    last_target: str | None = None
    last_backend: str | None = None
    last_error: str | None = None


_job = _Job()
_translate_sem: asyncio.Semaphore | None = None   # lazy-init on first request


def _init_sem() -> asyncio.Semaphore:
    global _translate_sem
    if _translate_sem is None:
        _translate_sem = asyncio.Semaphore(1)
    return _translate_sem


# ---------------------------------------------------------------------------
# Config Pydantic model (for PATCH /api/config)
# ---------------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    backend:              Optional[str] = None
    ollama_url:           Optional[str] = None
    ollama_model:         Optional[str] = None
    ollama_system_prompt: Optional[str] = None
    ollama_key:           Optional[str] = None
    libre_url:            Optional[str] = None
    libre_key:            Optional[str] = None


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

_API_DESCRIPTION = """
Self-hosted PDF translation service.

**Quick test with curl:**
```bash
curl -X POST http://localhost:7860/api/translate \\\\
  -F "file=@document.pdf" \\\\
  -F "source=nl" -F "target=en" \\\\
  --output translated.pdf
```
"""

api_app = FastAPI(
    title="pdf-translate",
    description=_API_DESCRIPTION,
    version=_VERSION,
)


@api_app.get("/api/health", summary="Health check", tags=["Status"])
async def api_health():
    """Return service status, version, default backend, and current job state.

    `job.running` is true while a translation is in progress.
    `job.last` contains the outcome of the most recent job (persists until the
    next job starts) — use this to check what happened after a client timeout.
    """
    cfg = load_config()
    if _job.running:
        job_info: dict = {
            "running": True,
            "queued": _job.queued,
            "started_at": _job.started_at,
            "source": _job.source,
            "target": _job.target,
            "backend": _job.backend,
        }
    else:
        last = None
        if _job.last_status is not None:
            last = {
                "status": _job.last_status,
                "started_at": _job.last_started_at,
                "completed_at": _job.last_completed_at,
                "source": _job.last_source,
                "target": _job.last_target,
                "backend": _job.last_backend,
            }
            if _job.last_error:
                last["error"] = _job.last_error
        job_info = {"running": False, "last": last}
    return {"status": "ok", "version": _VERSION, "backend": cfg["backend"], "job": job_info}


@api_app.get("/api/config", summary="Read configuration", tags=["Configuration"])
async def api_config_get():
    """Return the current backend configuration. API key fields are masked."""
    cfg = load_config()
    for key in ("ollama_key", "libre_key"):
        if cfg.get(key):
            cfg[key] = "***"
    return cfg


def _classify_error(msg: str) -> str:
    """Map an error message string to a machine-readable error_type for automation."""
    if "GGML_ASSERT" in msg:
        return "ocr_model_error"
    if "CUDA OOM" in msg:
        return "ocr_oom"
    if "429 TOO MANY REQUESTS" in msg or "429 Too Many Requests" in msg:
        return "translation_rate_limit"
    if "Connection refused" in msg or "ConnectError" in msg:
        return "config_error"
    return "translation_error"


@api_app.patch("/api/config", summary="Update configuration", tags=["Configuration"])
async def api_config_patch(update: ConfigUpdate):
    """
    Partially update the backend configuration.

    Only supplied (non-null) fields are changed; omitted fields keep their
    current values. Changes are persisted to config.json and take effect
    immediately — no restart required.
    """
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields supplied.")
    update_config(updates)
    return {"status": "ok", "updated": list(updates.keys())}


@api_app.delete("/api/translate", summary="Cancel running translation", tags=["Translation"])
async def api_translate_cancel():
    """
    Request cancellation of the currently running translation.

    Cancellation is best-effort: the in-progress backend call (Ollama /
    LibreTranslate / Google) for the current block runs to completion, then
    the pipeline stops. The blocked `POST /api/translate` call returns 409.
    """
    if not _job.running:
        raise HTTPException(status_code=404, detail="No translation is currently running.")
    _job.cancel_event.set()
    return {
        "status": "ok",
        "message": "Cancellation requested. Translation stops after the current block.",
    }


@api_app.post("/api/translate", summary="Translate a PDF", tags=["Translation"])
async def api_translate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF file to translate"),
    source: str = Form("auto", description="Source language code: 'auto', 'en', 'nl', 'de', …"),
    target: str = Form("en", description="Target language code: 'en', 'nl', 'de', 'fr', …"),
    service: Optional[str] = Form(
        None,
        description="Backend: Ollama, LibreTranslate, or Google. Defaults to saved config.",
    ),
    allow_wrap: bool = Form(
        False,
        description="Collapse line breaks before translation (helps with wrapped paragraphs)",
    ),
    filter_icons: bool = Form(
        True,
        description="Strip single-character icon glyphs from mixed text blocks",
    ),
    merge_blocks: bool = Form(
        False,
        description="Merge same-line word fragments before translation. "
                    "Enable for DTP/InDesign PDFs with word-level text objects.",
    ),
    detect_tables: bool = Form(
        True,
        description="Detect table cells and apply shrink-to-fit text fitting. "
                    "Disable if translated text appears abnormally small.",
    ),
    force_ocr: bool = Form(
        False,
        description="Ignore the text layer and OCR every page as an image. "
                    "Use for mixed PDFs where the text layer is incomplete.",
    ),
    outputs: Literal["pdf", "sbs", "reading", "all"] = Form(
        "pdf",
        description=(
            "Output format: "
            "'pdf' — translated PDF (default); "
            "'sbs' — side-by-side landscape PDF, layout-matched (original | translation); "
            "'reading' — HTML reading view, original and translated text side by side, clean and reflowable; "
            "'all' — zip archive containing all three."
        ),
    ),
):
    """
    Upload a PDF and receive the translated output.

    **This call is synchronous** — the HTTP connection stays open until
    translation completes. Set your client timeout to >= 300 s for large
    documents. If running behind a reverse proxy, raise its read timeout too
    (nginx: `proxy_read_timeout 300;`).

    **Queue:** one translation runs at a time; one additional request may wait.
    A third concurrent request receives **429** with a `Retry-After: 60` header.

    **Cancellation:** send `DELETE /api/translate` to stop the running job.
    The blocked POST returns **409** when cancelled.

    **Status codes:**
    - 200 — success; body is the requested output
    - 409 — translation was cancelled via `DELETE /api/translate`
    - 422 — no translatable text blocks found in the PDF
    - 429 — queue full (1 running + 1 waiting); retry after 60 s
    - 500 — backend error (connection failure, model error, etc.)

    Check `GET /api/health` after a timeout to see what happened to the job.
    Response headers always include `X-Source-Lang`, `X-Target-Lang`, `X-Backend`.
    """
    cfg = load_config()
    backend = service or cfg["backend"]

    # Save upload to a temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_pdf = tmp.name

    # Queue management — no await between check and update: atomic in event loop
    if _job.running and _job.queued >= 1:
        os.unlink(tmp_pdf)
        raise HTTPException(
            status_code=429,
            detail="Translation queue is full (1 running, 1 waiting). Retry later.",
            headers={"Retry-After": "60"},
        )
    if _job.running:
        _job.queued += 1

    sem = _init_sem()
    started_at = datetime.now(timezone.utc).isoformat()
    job_status = "completed"
    error_msg: str | None = None
    translated_path = sbs_path = reading_path = None

    try:
        async with sem:
            if _job.queued > 0:
                _job.queued -= 1

            # Activate job state
            _job.running = True
            _job.started_at = started_at
            _job.source = source
            _job.target = target
            _job.backend = backend
            _job.cancel_event.clear()

            try:
                translated_path, sbs_path, reading_path = await asyncio.to_thread(
                    translate_sync,
                    tmp_pdf, source, target, backend,
                    allow_wrap=allow_wrap,
                    filter_icons=filter_icons,
                    cancel_event=_job.cancel_event,
                    merge_blocks=merge_blocks,
                    detect_tables=detect_tables,
                    force_ocr=force_ocr,
                )
            except InterruptedError:
                job_status = "cancelled"
            except ValueError as exc:
                job_status = "failed"
                error_msg = str(exc)
            except Exception as exc:
                job_status = "failed"
                error_msg = str(exc)
            finally:
                # Reset current job; persist last result
                _job.running = False
                _job.started_at = None
                _job.last_status = job_status
                _job.last_started_at = started_at
                _job.last_completed_at = datetime.now(timezone.utc).isoformat()
                _job.last_source = source
                _job.last_target = target
                _job.last_backend = backend
                _job.last_error = error_msg
    finally:
        os.unlink(tmp_pdf)

    if job_status == "cancelled":
        return JSONResponse(status_code=409, content={"detail": "Translation was cancelled.", "error_type": "cancelled"})
    if job_status == "failed":
        if error_msg and "No translatable" in error_msg:
            return JSONResponse(status_code=422, content={"detail": error_msg, "error_type": "no_text"})
        detail = f"Translation failed: {error_msg}"
        return JSONResponse(status_code=500, content={"detail": detail, "error_type": _classify_error(error_msg or "")})

    stem = Path(file.filename or "document").stem
    extra = {"X-Source-Lang": source, "X-Target-Lang": target, "X-Backend": backend}
    # Schedule cleanup of the output temp dir after the response is sent.
    out_dir = str(Path(translated_path).parent)
    background_tasks.add_task(shutil.rmtree, out_dir, True)

    if outputs == "pdf":
        return FileResponse(
            translated_path,
            media_type="application/pdf",
            filename=f"{stem}_{target}.pdf",
            headers=extra,
            background=background_tasks,
        )
    if outputs == "sbs":
        return FileResponse(
            sbs_path,
            media_type="application/pdf",
            filename=f"{stem}_{source}_{target}_sbs.pdf",
            headers=extra,
            background=background_tasks,
        )
    if outputs == "reading":
        return FileResponse(
            reading_path,
            media_type="text/html",
            filename=f"{stem}_{source}_{target}_reading.html",
            headers=extra,
            background=background_tasks,
        )
    # outputs == "all" — zip all three into memory, then clean up
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(translated_path, f"{stem}_{target}.pdf")
        zf.write(sbs_path,        f"{stem}_{source}_{target}_sbs.pdf")
        zf.write(reading_path,    f"{stem}_{source}_{target}_reading.html")
    buf.seek(0)
    zip_headers = {
        **extra,
        "Content-Disposition": f'attachment; filename="{stem}_{target}.zip"',
    }
    return Response(content=buf.read(), media_type="application/zip", headers=zip_headers)


# ---------------------------------------------------------------------------
# Mount Gradio at root, then launch
# ---------------------------------------------------------------------------

app = gr.mount_gradio_app(
    api_app,
    gradio_app,
    path="/",
    favicon_path=str(Path(__file__).parent / "icon.svg"),
    css="label.disabled { opacity: 0.4 !important; color: var(--body-text-color-subdued, #9ca3af) !important; }",
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
