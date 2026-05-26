"""
pdf-translate — backends package.

Public API (imported by app.py):
  translate(...)               generator dispatcher (Gradio UI)
  translate_sync(...)          synchronous dispatcher (REST API)
  test_ollama_connection(...)  → str
  test_libre_connection(...)   → str

Adding a new backend
--------------------
1. Create backends/<name>.py with:
   - call(text, source, target, **params) -> str
   - test_connection(...) -> str  (optional)
2. Add an elif branch in both translate() and translate_sync() below.
   Each branch builds a call_fn closure and a meta dict, then delegates to
   pipeline — no per-backend translate_pdf() wrapper needed.
3. Add any new UI inputs in app.py and thread them through.
"""

import threading

import gradio as gr

import pipeline as _pipeline
from backends import google, libretranslate, ollama
from config import load_config

# Re-export test functions under the names app.py expects
test_ollama_connection = ollama.test_connection
test_libre_connection  = libretranslate.test_connection


def translate(
    pdf_path: str,
    source_lang: str,
    target_lang: str,
    service: str,
    ollama_url: str,
    ollama_model: str,
    ollama_system_prompt: str,
    ollama_key: str,
    libre_url: str,
    libre_key: str,
    allow_wrap: bool = False,
    filter_icons: bool = True,
    ocr_service: str = "Tesseract",
    ocr_ollama_model: str = "glm-ocr",
    ocr_ollama_prompt: str = "",
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
):
    """
    Generator dispatcher — builds a call_fn closure per backend, then yields
    from pipeline.translate_pdf_generic.  Yields 11-tuple; see pipeline for
    field order.
    """
    if not pdf_path:
        raise gr.Error("Please upload a PDF file.")

    ocr_config = {"url": ollama_url, "model": ocr_ollama_model, "prompt": ocr_ollama_prompt}

    if service == "LibreTranslate":
        def call_fn(text: str, src: str, tgt: str) -> str:
            return libretranslate.call(text, src, tgt, libre_url, libre_key)
        meta = {"service": service, "url": libre_url}
    elif service == "Ollama":
        def call_fn(text: str, src: str, tgt: str) -> str:
            return ollama.call(text, src, tgt, ollama_url, ollama_model, ollama_system_prompt, ollama_key)
        meta = {"service": service, "url": ollama_url, "model": ollama_model}
    elif service == "Google":
        call_fn = google.call
        meta = {"service": service, "url": "https://translate.googleapis.com"}
    else:
        raise gr.Error(f"Unknown backend: {service}")

    yield from _pipeline.translate_pdf_generic(
        pdf_path, source_lang, target_lang, service, call_fn, allow_wrap, filter_icons,
        meta=meta, ocr_service=ocr_service, ocr_config=ocr_config,
        merge_blocks=merge_blocks, detect_tables=detect_tables,
        force_ocr=force_ocr,
    )


def translate_sync(
    pdf_path: str,
    source_lang: str,
    target_lang: str,
    service: str,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    ollama_system_prompt: str | None = None,
    ollama_key: str | None = None,
    libre_url: str | None = None,
    libre_key: str | None = None,
    allow_wrap: bool = False,
    filter_icons: bool = True,
    cancel_event: threading.Event | None = None,
    ocr_service: str | None = None,
    ocr_ollama_model: str | None = None,
    ocr_ollama_prompt: str | None = None,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
) -> tuple[str, str, str]:
    """
    Synchronous (non-generator) translation for the REST API.

    Backend-specific params default to config.json values when not supplied.
    If cancel_event is set mid-translation, raises InterruptedError after the
    current block's backend call completes (best-effort cancellation).
    Returns (translated_pdf_path, sbs_pdf_path, html_path).
    Raises ValueError for unknown backend or empty document.
    """
    import pipeline as _pipeline

    cfg = load_config()

    # OCR settings — fall back to config values
    _ocr_service = ocr_service or cfg.get("ocr_service", "Tesseract")
    _ocr_url     = ollama_url or cfg["ollama_url"]
    _ocr_model   = ocr_ollama_model or cfg.get("ocr_ollama_model", "glm-ocr")
    _ocr_prompt  = ocr_ollama_prompt or cfg.get("ocr_ollama_prompt", "")
    ocr_cfg      = {"url": _ocr_url, "model": _ocr_model, "prompt": _ocr_prompt}

    if service == "LibreTranslate":
        url = libre_url or cfg["libre_url"]
        key = libre_key if libre_key is not None else cfg["libre_key"]
        def call_fn(text: str, src: str, tgt: str) -> str:
            return libretranslate.call(text, src, tgt, url, key)
    elif service == "Ollama":
        url    = ollama_url    or cfg["ollama_url"]
        model  = ollama_model  or cfg["ollama_model"]
        prompt = ollama_system_prompt or cfg["ollama_system_prompt"]
        key    = ollama_key if ollama_key is not None else cfg["ollama_key"]
        def call_fn(text: str, src: str, tgt: str) -> str:
            return ollama.call(text, src, tgt, url, model, prompt, key)
    elif service == "Google":
        call_fn = google.call
    else:
        raise ValueError(f"Unknown backend: {service!r}")

    # Wrap call_fn with cancellation support — checked between blocks
    if cancel_event is not None:
        _raw = call_fn
        def call_fn(text: str, src: str, tgt: str) -> str:  # noqa: E731
            if cancel_event.is_set():
                raise InterruptedError("Translation cancelled.")
            return _raw(text, src, tgt)

    return _pipeline.translate_pdf_sync(
        pdf_path, source_lang, target_lang, service, call_fn, allow_wrap, filter_icons,
        ocr_service=_ocr_service, ocr_config=ocr_cfg,
        merge_blocks=merge_blocks, detect_tables=detect_tables,
        force_ocr=force_ocr,
    )
