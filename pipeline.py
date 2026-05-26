"""
pdf-translate — shared translation pipeline.

translate_pdf_generic is the single generator that all backends delegate to.
It owns: prescan → progress reporting → block-by-block translation → output writing.

Adding a new backend means writing a backend module that creates a call_fn
closure and does `yield from pipeline.translate_pdf_generic(...)`.
"""

import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import gradio as gr

_DEBUG = os.environ.get("PDF_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")

# Delay between LibreTranslate API calls in milliseconds.
# Prevents 429 / rate-limit errors on self-hosted LibreTranslate instances.
# Only applied when the LibreTranslate backend is active.
# Set to 0 to disable.  Default: 200 ms (~5 req/s).
_LIBRE_BLOCK_DELAY_MS = int(os.environ.get("LIBRETRANSLATE_BLOCK_DELAY_MS", "200"))

# Directory for debug log files — same as the config/data directory.
_DATA_DIR = Path(__file__).parent / "data"

from pdf_utils import (
    generate_side_by_side_html,
    make_side_by_side_pdf,
    pdf_to_image,
    prescan_blocks,
    write_translated_pdf,
)

# ---------------------------------------------------------------------------
# Debug log
# ---------------------------------------------------------------------------


class _DebugLog:
    """Per-translation log file written to data/ when PDF_TRANSLATE_DEBUG=1.

    Each translation creates a new file: data/debug_YYYYMMDD_HHMMSS.log.
    All progress messages (normal + debug-only) are timestamped and appended.
    The file lives in the bind-mounted data directory so it is accessible on
    the host without exec'ing into the container.
    """

    def __init__(
        self,
        stem: str,
        service: str,
        source: str,
        target: str,
        ocr_service: str = "Tesseract",
        ocr_config: dict | None = None,
    ):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = _DATA_DIR / f"debug_{ts}_{stem}.log"
        self._f = self.path.open("w", encoding="utf-8")
        self._write(f"=== pdf-translate debug log ===")
        self._write(f"file    : {stem}")
        self._write(f"service : {service}  {source} → {target}")
        self._write(f"delay   : {_LIBRE_BLOCK_DELAY_MS} ms (LibreTranslate only)")
        ocr_label = ocr_service
        if ocr_service == "Ollama" and ocr_config:
            ocr_label = f"Ollama ({ocr_config.get('model', '?')})"
        self._write(f"ocr     : {ocr_label}")
        self._write("")

    def _write(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        self._f.write(line + "\n")
        self._f.flush()
        print(line, flush=True)          # also visible in docker logs

    def log(self, msg: str) -> None:
        self._write(msg)

    def close(self, summary: str) -> None:
        self._write("")
        self._write(summary)
        self._write(f"=== log: {self.path} ===")
        self._f.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prog(orig_image: str, msg: str) -> tuple:
    """
    Intermediate progress yield — 11-tuple matching translate() output count.
    Leaves all download components, page state, and nav buttons unchanged.
    """
    return (
        None, None, None, orig_image, None, msg,
        gr.update(), gr.update(), gr.update(),
        gr.update(), gr.update(),  # prev_btn, next_btn
    )


def _build_outputs(
    pdf_path: str,
    page_blocks: list[list[tuple]],
    page_translations: list[list[tuple]],
    source: str,
    target: str,
    stem: str,
    service: str,
    embedded_fonts: dict[str, bytes],
    meta: dict | None = None,
) -> tuple[str, str, str, str]:
    """
    Write all output files into a fresh temp dir.
    Returns (translated_pdf_path, sbs_pdf_path, reading_pdf_path, trans_image_path).
    """
    tmp_dir             = Path(tempfile.mkdtemp())
    translated_pdf_path = str(tmp_dir / f"{stem}_{target}.pdf")
    sbs_pdf_path        = str(tmp_dir / f"{stem}_{source}_{target}.pdf")
    html_path           = str(tmp_dir / f"{stem}_{source}_{target}.html")

    write_translated_pdf(pdf_path, page_translations, translated_pdf_path, embedded_fonts)
    html_content = generate_side_by_side_html(
        source, target, page_blocks, page_translations, stem=stem, meta=meta,
    )
    Path(html_path).write_text(html_content, encoding="utf-8")
    make_side_by_side_pdf(pdf_path, translated_pdf_path, output_path=sbs_pdf_path)
    trans_image = pdf_to_image(translated_pdf_path)

    return translated_pdf_path, sbs_pdf_path, html_path, trans_image


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_pdf_generic(
    pdf_path: str,
    source: str,
    target: str,
    service: str,
    call_fn,  # callable(text: str, source: str, target: str) -> str
    allow_wrap: bool = False,
    filter_icons: bool = True,
    meta: dict | None = None,
    ocr_service: str = "Tesseract",
    ocr_config: dict | None = None,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
):
    """
    Generic PDF translation generator shared by all backends.

    call_fn translates one text block; the caller creates a closure that
    captures backend-specific parameters (URL, model, key, …).

    Yields 11-tuple:
      (translated_pdf, sbs_pdf, html,
       orig_image, translated_image, status,
       page_state_reset, page_indicator, translated_path_state,
       prev_btn_update, next_btn_update)
    """
    orig_image = pdf_to_image(pdf_path)
    stem       = Path(pdf_path).stem

    log = _DebugLog(stem, service, source, target, ocr_service=ocr_service, ocr_config=ocr_config) if _DEBUG else None

    def _status(msg: str) -> None:
        if log:
            log.log(msg)

    yield _prog(orig_image, "Reading PDF…")
    _status("Reading PDF…")

    from ocr_utils import source_lang_to_tesseract  # lazy — optional dep
    ocr_lang = source_lang_to_tesseract(source)

    if _DEBUG:
        ocr_label = ocr_service
        if ocr_service == "Ollama" and ocr_config:
            ocr_label = f"Ollama ({ocr_config.get('model', '?')})"
        scan_mode = f"force_ocr={force_ocr}  ocr_service={ocr_label}  merge_blocks={merge_blocks}  detect_tables={detect_tables}"
        _status(f"[debug] prescan start  {scan_mode}")

    try:
        page_blocks, total_blocks, n_pages, embedded_fonts = prescan_blocks(
            pdf_path, filter_icons, ocr_lang=ocr_lang,
            ocr_service=ocr_service, ocr_config=ocr_config,
            merge_blocks=merge_blocks, detect_tables=detect_tables,
            force_ocr=force_ocr,
        )
    except Exception as exc:
        err_msg = f"[error] prescan failed: {type(exc).__name__}: {exc}"
        if log:
            log.log(err_msg)
            log.close("ABORTED in prescan")
        raise gr.Error(f"PDF scan failed: {exc}") from exc

    if total_blocks == 0:
        if log:
            log.close("ERROR: no translatable text blocks found")
        raise gr.Error("No translatable text blocks found in this PDF.")

    if _DEBUG:
        page_detail = "  ".join(f"p{i+1}:{len(b)}" for i, b in enumerate(page_blocks))
        dbg_msg = f"[debug] {n_pages}p  {total_blocks} blocks  ocr={ocr_service}\n{page_detail}"
        yield _prog(orig_image, dbg_msg)
        _status(dbg_msg)
        # Per-page fontsize range — reveals OCR fontsize estimation issues
        for pi, pblocks in enumerate(page_blocks):
            if pblocks:
                sizes = [b[5] for b in pblocks]
                fonts = {b[6] for b in pblocks}
                _status(f"[debug] p{pi+1} fontsize={min(sizes):.1f}–{max(sizes):.1f}  fonts={fonts}")

    found_msg = f"Found {total_blocks} block(s) across {n_pages} page(s). Starting…"
    yield _prog(orig_image, found_msg)
    _status(found_msg)

    done = 0
    page_translations: list[list[tuple]] = []

    for p, blocks in enumerate(page_blocks):
        translated_blocks: list[tuple] = []
        for i, (x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell) in enumerate(blocks):
            done += 1
            remaining = total_blocks - done
            prog_msg = f"Page {p + 1}/{n_pages} — block {i + 1}/{len(blocks)} ({remaining} remaining)"
            yield _prog(orig_image, prog_msg)
            if _DEBUG:
                _status(f"{prog_msg}  size={fontsize:.1f}  font={orig_font_name!r}  text={repr(text[:50])}")
            else:
                _status(prog_msg)

            if allow_wrap:
                text = " ".join(text.split("\n"))
            try:
                translated = call_fn(text, source, target)
            except Exception as exc:
                err_msg = f"[error] p{p+1} block {i+1}: {exc}"
                if log:
                    log.log(err_msg)
                if _DEBUG:
                    yield _prog(orig_image, err_msg)
                raise
            if service == "LibreTranslate" and _LIBRE_BLOCK_DELAY_MS > 0:
                time.sleep(_LIBRE_BLOCK_DELAY_MS / 1000)
            translated_blocks.append((x0, y0, x1, y1, translated, fontsize, orig_font_name, flags, is_table_cell))
        page_translations.append(translated_blocks)

    writing_msg = "Writing outputs…"
    yield _prog(orig_image, writing_msg)
    _status(writing_msg)

    translated_pdf_path, sbs_pdf_path, reading_pdf_path, trans_image = _build_outputs(
        pdf_path, page_blocks, page_translations, source, target, stem, service, embedded_fonts,
        meta=meta,
    )

    done_msg = f"Done — {total_blocks} block(s) translated across {n_pages} page(s)."
    if log:
        log.close(done_msg)
        done_msg += f"\nDebug log: {log.path}"

    yield (
        gr.update(value=translated_pdf_path,  visible=True),
        gr.update(value=sbs_pdf_path,         visible=True),
        gr.update(value=reading_pdf_path,     visible=True),
        orig_image,
        trans_image,
        done_msg,
        0,
        f"Page 1 / {n_pages}",
        translated_pdf_path,
        gr.update(visible=True),   # prev_btn
        gr.update(visible=True),   # next_btn
    )


# ---------------------------------------------------------------------------
# Synchronous API (no Gradio yields)
# ---------------------------------------------------------------------------


def translate_pdf_sync(
    pdf_path: str,
    source: str,
    target: str,
    service: str,
    call_fn,  # callable(text: str, source: str, target: str) -> str
    allow_wrap: bool = False,
    filter_icons: bool = True,
    meta: dict | None = None,
    ocr_service: str = "Tesseract",
    ocr_config: dict | None = None,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
) -> tuple[str, str, str]:
    """
    Synchronous PDF translation for the REST API.

    Same logic as translate_pdf_generic but without Gradio yields.
    Returns (translated_pdf_path, sbs_pdf_path, html_path).
    Raises ValueError if no translatable text blocks are found.
    """
    stem = Path(pdf_path).stem
    log = _DebugLog(stem, service, source, target, ocr_service=ocr_service, ocr_config=ocr_config) if _DEBUG else None

    from ocr_utils import source_lang_to_tesseract  # lazy — optional dep
    ocr_lang = source_lang_to_tesseract(source)

    if log:
        _ocr_label = ocr_service
        if ocr_service == "Ollama" and ocr_config:
            _ocr_label = f"Ollama ({ocr_config.get('model', '?')})"
        log.log(f"[prescan] force_ocr={force_ocr}  ocr_service={_ocr_label}  "
                f"merge_blocks={merge_blocks}  detect_tables={detect_tables}")

    try:
        page_blocks, total_blocks, n_pages, embedded_fonts = prescan_blocks(
            pdf_path, filter_icons, ocr_lang=ocr_lang,
            ocr_service=ocr_service, ocr_config=ocr_config,
            merge_blocks=merge_blocks, detect_tables=detect_tables,
            force_ocr=force_ocr,
        )
    except Exception as exc:
        if log:
            log.log(f"[error] prescan failed: {type(exc).__name__}: {exc}")
            log.close("ABORTED in prescan")
        raise

    if total_blocks == 0:
        if log:
            log.close("ERROR: no translatable text blocks found")
        raise ValueError("No translatable text blocks found in this PDF.")

    if log:
        page_detail = "  ".join(f"p{i+1}:{len(b)}" for i, b in enumerate(page_blocks))
        log.log(f"{n_pages}p  {total_blocks} blocks  ocr={ocr_service}\n{page_detail}")
        # Per-page fontsize range
        for pi, pblocks in enumerate(page_blocks):
            if pblocks:
                sizes = [b[5] for b in pblocks]
                fonts = {b[6] for b in pblocks}
                log.log(f"[prescan] p{pi+1} fontsize={min(sizes):.1f}–{max(sizes):.1f}  fonts={fonts}")

    page_translations: list[list[tuple]] = []
    for p, blocks in enumerate(page_blocks):
        translated_blocks: list[tuple] = []
        for i, (x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell) in enumerate(blocks):
            if log:
                log.log(f"p{p+1} block {i+1}/{len(blocks)}  size={fontsize:.1f}  font={orig_font_name!r}  text={repr(text[:40])}")
            if allow_wrap:
                text = " ".join(text.split("\n"))
            try:
                translated = call_fn(text, source, target)
            except Exception as exc:
                if log:
                    log.log(f"[error] p{p+1} block {i+1}: {exc}")
                    log.close("ABORTED on error")
                raise
            if service == "LibreTranslate" and _LIBRE_BLOCK_DELAY_MS > 0:
                time.sleep(_LIBRE_BLOCK_DELAY_MS / 1000)
            translated_blocks.append(
                (x0, y0, x1, y1, translated, fontsize, orig_font_name, flags, is_table_cell)
            )
        page_translations.append(translated_blocks)

    translated_pdf_path, sbs_pdf_path, reading_pdf_path, _ = _build_outputs(
        pdf_path, page_blocks, page_translations, source, target, stem, service, embedded_fonts,
        meta=meta,
    )
    if log:
        log.close(f"Done — {total_blocks} block(s) translated across {n_pages} page(s).")
    return translated_pdf_path, sbs_pdf_path, reading_pdf_path
