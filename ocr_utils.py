"""
pdf-translate — OCR utilities for scanned PDFs.

Covers:
  source_lang_to_tesseract  map ISO 639-1 source code to Tesseract lang string
  is_scanned_page            detect whether a page lacks a usable text layer
  ocr_page                   run Tesseract OCR and return 9-tuples matching prescan output
  ocr_page_llm               run vision-LLM OCR via Ollama and return 9-tuples

pytesseract and Pillow are imported lazily inside ocr_page() so that the module
can be imported without those packages when OCR is not used.
httpx is imported lazily inside ocr_page_llm().
"""

import io
import os
import unicodedata

import fitz  # PyMuPDF

_DEBUG = os.environ.get("PDF_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Language code mapping
# ---------------------------------------------------------------------------

# ISO 639-1 → Tesseract language code.
# "auto" uses multi-language mode covering all packs installed in the Dockerfile.
_AUTO_LANG = "eng+nld+deu+fra+spa+por+ita"

_LANG_MAP: dict[str, str] = {
    "auto": _AUTO_LANG,
    "en":   "eng",
    "nl":   "nld",
    "de":   "deu",
    "fr":   "fra",
    "es":   "spa",
    "it":   "ita",
    "pt":   "por",
    "ru":   "rus",
    "ja":   "jpn",
    "zh":   "chi_sim",
    "ko":   "kor",
    "ar":   "ara",
    "tr":   "tur",
    "pl":   "pol",
    "tl":   "tgl",
}


def source_lang_to_tesseract(lang: str) -> str:
    """Return the Tesseract language string for a given ISO 639-1 source code.

    Falls back to English for unknown codes.  "auto" returns a multi-language
    string covering the packs installed in the Dockerfile.
    """
    return _LANG_MAP.get(lang, "eng")


# ---------------------------------------------------------------------------
# Scanned-page detection
# ---------------------------------------------------------------------------

#: Pages with fewer than this many non-whitespace characters are treated as scanned.
MIN_VISIBLE_CHARS = 20

_SKIP_CATEGORIES = frozenset(("Zs", "Zl", "Zp", "Cc", "Cf"))


def is_scanned_page(page: fitz.Page) -> bool:
    """Return True if the page has fewer than MIN_VISIBLE_CHARS visible characters.

    Uses the same Unicode whitespace filter as prescan_blocks so that pages
    with only non-breaking spaces or format characters are correctly identified
    as scanned.
    """
    visible = "".join(
        c for c in page.get_text()
        if unicodedata.category(c) not in _SKIP_CATEGORIES
    )
    return len(visible) < MIN_VISIBLE_CHARS


# ---------------------------------------------------------------------------
# OCR extraction — Tesseract
# ---------------------------------------------------------------------------

#: DPI used when rendering a page for Tesseract (300 is the recommended minimum).
OCR_DPI = 300


def ocr_page(page: fitz.Page, lang: str = "eng") -> list[tuple]:
    """Run Tesseract OCR on a page and return paragraph-level 9-tuples.

    Tuple format matches prescan_blocks output:
      (x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell)

    Words are grouped by Tesseract's (block_num, par_num) — one tuple per
    paragraph.  This keeps the number of blocks (and therefore translation API
    calls) comparable to a native-text PDF, and gives the translator full
    sentences rather than isolated lines.

    Bounding boxes are in PDF point coordinates (72 dpi base), scaled back from
    the OCR render resolution.  fontsize is a fixed 10 pt document default —
    per-glyph metrics are unavailable from Tesseract, and height-based estimates
    are unreliable for lowercase text without ascenders.
    orig_font_name is "OCR"; flags and is_table_cell are always 0 / False.

    pytesseract and Pillow are imported here (lazy) so that the module can be
    used without those packages when OCR is not invoked.
    """
    import pytesseract  # noqa: PLC0415 — lazy import
    from PIL import Image  # noqa: PLC0415 — lazy import

    scale = OCR_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = Image.open(io.BytesIO(pix.tobytes("png")))

    data = pytesseract.image_to_data(
        img,
        lang=lang,
        output_type=pytesseract.Output.DICT,
    )

    if _DEBUG:
        print(f"[ocr_page] p{page.number + 1}  lang={lang}  dpi={OCR_DPI}  "
              f"img={img.width}×{img.height}px", flush=True)

    # Group words into paragraphs keyed by (block_num, par_num).
    # Tesseract returns words in reading order, so insertion order is preserved.
    paragraphs: dict[tuple, dict] = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = int(data["conf"][i])
        if conf <= 0 or not word:
            continue

        key = (data["block_num"][i], data["par_num"][i])
        lx0 = data["left"][i]
        ly0 = data["top"][i]
        lx1 = lx0 + data["width"][i]
        ly1 = ly0 + data["height"][i]

        if key not in paragraphs:
            paragraphs[key] = {
                "words": [word],
                "x0":    lx0,
                "y0":    ly0,
                "x1":    lx1,
                "y1":    ly1,
            }
        else:
            entry = paragraphs[key]
            entry["words"].append(word)
            entry["x0"] = min(entry["x0"], lx0)
            entry["y0"] = min(entry["y0"], ly0)
            entry["x1"] = max(entry["x1"], lx1)
            entry["y1"] = max(entry["y1"], ly1)

    if _DEBUG:
        print(f"[ocr_page] p{page.number + 1}  paragraphs={len(paragraphs)}", flush=True)

    blocks: list[tuple] = []
    for entry in paragraphs.values():
        text = " ".join(entry["words"]).strip()
        if not text:
            continue
        # Scale bounding box from OCR pixel coords back to PDF point coords
        x0 = entry["x0"] / scale
        y0 = entry["y0"] / scale
        x1 = entry["x1"] / scale
        y1 = entry["y1"] / scale
        # Use a fixed document default fontsize for OCR blocks.
        # Per-word height estimates are unreliable: lowercase letters without
        # ascenders have small visual bounding boxes, consistently under-reporting
        # the rendered size.  10 pt is a reasonable body-text default and avoids
        # layout artefacts from wildly varying font sizes across OCR blocks.
        fontsize = 10.0
        if _DEBUG:
            print(f"[ocr_page]   blk text={repr(text[:40])}  "
                  f"bbox=({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})  "
                  f"fontsize={fontsize:.1f}  words={len(entry['words'])}", flush=True)
        blocks.append((x0, y0, x1, y1, text, fontsize, "OCR", 0, False))

    return blocks


# ---------------------------------------------------------------------------
# OCR extraction — vision LLM (Ollama)
# ---------------------------------------------------------------------------

#: DPI for rendering pages sent to a vision LLM.  150 gives good quality at
#: moderate image size (~1–2 MB PNG); higher values slow the model down.
OCR_LLM_DPI = 150

#: Default vision model used for LLM-based OCR.
#: glm-ocr is a purpose-built 1.1B OCR model — much smaller VRAM footprint than
#: general vision models (minicpm-v 7.6B) and designed specifically for document OCR.
OCR_LLM_DEFAULT_MODEL = "glm-ocr"

#: Default prompt sent with the page image.
OCR_LLM_DEFAULT_PROMPT = (
    "Extract all text from this document image. "
    "Return only the text content, preserving paragraph structure with blank lines "
    "between paragraphs. Do not add explanations, formatting, or markdown."
)

#: Timeout for vision LLM inference (seconds) — vision models are slower than text models.
OCR_LLM_TIMEOUT = 120.0

#: Page margins used when assigning approximate bounding boxes for LLM OCR output.
#: Values are in PDF points (72 pt = 1 inch).  50 pt ≈ 17.6 mm — a standard body-text
#: margin for A4 and Letter.  Increase if translated text overflows into the page edge.
OCR_LLM_MARGIN_X = 50.0
OCR_LLM_MARGIN_Y = 50.0


def ocr_page_llm(
    page: fitz.Page,
    url: str,
    model: str = OCR_LLM_DEFAULT_MODEL,
    prompt: str = OCR_LLM_DEFAULT_PROMPT,
) -> list[tuple]:
    """Run vision-LLM OCR on a page via Ollama and return paragraph-level 9-tuples.

    Tuple format matches prescan_blocks output:
      (x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell)

    The page is rendered to PNG and sent to the Ollama /api/chat endpoint.
    The model's response is split into paragraphs at blank lines.  Since a
    vision LLM returns text only (no layout coordinates), approximate stacked
    bounding boxes are assigned by dividing the page height evenly across
    paragraphs.  orig_font_name is "OCR-LLM"; flags and is_table_cell are 0/False.

    httpx is imported lazily so the module can be used without it when this
    function is not called.
    """
    import base64  # stdlib

    import httpx  # noqa: PLC0415 — lazy import

    # Render page to PNG at OCR_LLM_DPI
    scale = OCR_LLM_DPI / 72.0
    pix   = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    img_b64 = base64.b64encode(pix.tobytes("png")).decode()

    if _DEBUG:
        print(f"[ocr_page_llm] p{page.number + 1}  model={model}  "
              f"url={url}  img={pix.width}×{pix.height}px  timeout={OCR_LLM_TIMEOUT}s",
              flush=True)

    # Call Ollama vision API
    endpoint = f"{url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
        "stream": False,
    }
    try:
        r = httpx.post(endpoint, json=payload, timeout=OCR_LLM_TIMEOUT)
        if _DEBUG:
            print(f"[ocr_page_llm] p{page.number + 1}  HTTP {r.status_code}", flush=True)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if _DEBUG:
            print(f"[ocr_page_llm] p{page.number + 1}  HTTP error: {exc.response.status_code} "
                  f"{exc.response.text[:200]}", flush=True)
        # Surface a friendlier message for GPU out-of-memory errors so the user
        # knows to switch to a smaller OCR model (e.g. glm-ocr instead of minicpm-v).
        body = exc.response.text
        if exc.response.status_code == 500:
            if "cudaMalloc" in body:
                raise RuntimeError(
                    f"Ollama OCR model '{model}' ran out of GPU memory (CUDA OOM). "
                    f"Switch to a smaller model such as 'glm-ocr' in the Ollama OCR model setting."
                ) from exc
            if "GGML_ASSERT" in body:
                raise RuntimeError(
                    f"Ollama OCR model '{model}' hit an internal assertion error (GGML_ASSERT). "
                    f"This is a model compatibility issue with this specific page/image. "
                    f"Try a different OCR model such as 'deepseek-ocr' or 'minicpm-v'."
                ) from exc
        raise
    except Exception as exc:
        if _DEBUG:
            print(f"[ocr_page_llm] p{page.number + 1}  request failed: {type(exc).__name__}: {exc}",
                  flush=True)
        raise

    try:
        raw_text = r.json()["message"]["content"].strip()
    except (KeyError, ValueError) as exc:
        if _DEBUG:
            print(f"[ocr_page_llm] p{page.number + 1}  unexpected response shape: {exc}  "
                  f"body={r.text[:200]}", flush=True)
        raise

    if _DEBUG:
        print(f"[ocr_page_llm] p{page.number + 1}  raw_text={len(raw_text)} chars  "
              f"preview={repr(raw_text[:80])}", flush=True)

    if not raw_text:
        return []

    # Split into paragraphs at blank lines
    paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    if _DEBUG:
        print(f"[ocr_page_llm] p{page.number + 1}  {len(paragraphs)} paragraphs", flush=True)

    # Assign stacked approximate bounding boxes with standard page margins.
    # LLM OCR returns text only (no coordinates), so we divide the content area
    # (page minus margins) into equal-height slices — one per paragraph.
    # fontsize uses a fixed document default (11 pt) — no per-character metrics.
    page_w = page.rect.width
    page_h = page.rect.height
    n      = len(paragraphs)
    # Standard margins: 50 pt ≈ 17.6 mm (roughly A4 / Letter body-text margin).
    mx = OCR_LLM_MARGIN_X
    my = OCR_LLM_MARGIN_Y
    content_h = page_h - 2 * my
    slice_h   = content_h / n

    blocks: list[tuple] = []
    for i, text in enumerate(paragraphs):
        x0 = mx
        y0 = my + i * slice_h
        x1 = page_w - mx
        y1 = my + (i + 1) * slice_h
        fontsize = 11.0   # LLM OCR has no per-character metrics; use document default
        blocks.append((x0, y0, x1, y1, text, fontsize, "OCR-LLM", 0, False))

    return blocks
