"""
pdf-translate — PDF utilities.

Covers:
  prescan_blocks            extract translatable text blocks from a PDF
  write_translated_pdf      write a new PDF with translated text overlaid
  pdf_to_image              render one page to PNG for Gradio preview
  make_side_by_side_pdf     landscape PDF with original | translation per page
  generate_side_by_side_html  HTML reading view with metadata header
"""

import html as _html
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF

from fonts import extract_doc_fonts, normalize_font_name, resolve_font, _BASE14, _family_from_name, _span_style

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREVIEW_DPI    = 100   # DPI for Gradio preview thumbnails
SBS_DPI        = 150   # DPI for side-by-side PDF pixmaps
RENDER_DPI     = 150   # DPI for image background in translated PDF
RIGHT_MARGIN   = 10    # pts gap between expanded right edge and page boundary
DEBUG_FONTS    = False  # Set True to print per-page font registry to console
DEBUG_PRESCAN  = False  # Set True to print every block's font name + text during prescan


# ---------------------------------------------------------------------------
# Block extraction
# ---------------------------------------------------------------------------

def _is_horizontal(line: dict) -> bool:
    """Return True if the line direction is left-to-right horizontal."""
    dx, dy = line.get("dir", (1.0, 0.0))
    return abs(dx - 1.0) < 0.05 and abs(dy) < 0.05


_SYMBOL_FONT_KEYWORDS = frozenset([
    "wingding", "webding", "symbol", "dingbat", "zapfdingbat",
    "fontawesome", "materialicon", "glyphicon", "icomoon", "ionicon",
    "feather", "remixicon", "iconmoon", "nerd",
])


def _is_symbol_font(font_name: str) -> bool:
    """Return True if the font name suggests a symbol or icon font.

    Icon fonts (FontAwesome, Material Icons, etc.) map glyph codepoints to
    regular Unicode letter characters, so a Unicode-category check alone
    is not enough — the font name must be checked too.
    """
    clean = font_name.lower().replace("-", "").replace(" ", "").replace("_", "")
    return any(kw in clean for kw in _SYMBOL_FONT_KEYWORDS)


def _visible_chars(text: str) -> str:
    """Return text with all Unicode whitespace and control characters removed.

    Python's str.strip() only removes ASCII whitespace.  PDF text blocks often
    contain non-breaking spaces (U+00A0), Unicode separators (Zs/Zl/Zp), or
    format characters (Cf, e.g. zero-width space) that make an icon block look
    longer than 1 character to the length check.  This strips all of them.
    """
    skip = frozenset(("Zs", "Zl", "Zp", "Cc", "Cf"))
    return "".join(c for c in text if unicodedata.category(c) not in skip)


def _has_translatable_text(text: str, font_name: str = "") -> bool:
    """Return False for blocks that should not be sent to a translation backend.

    Skips:
    - Single-character blocks after full Unicode whitespace removal (icons,
      bullets, decorators, page numbers — including those padded with
      non-breaking spaces that Python's strip() would not remove).
    - Blocks containing any Private Use Area character (U+E000–F8FF) —
      modern icon fonts (FontAwesome, Material Icons, etc.) store glyphs
      in PUA regardless of their font name.
    - Blocks from known symbol/icon fonts (font name check).
    - Blocks containing only emoji, symbols, or digits — no Unicode letter.
    Mixed content (letters + real emoji) passes through as-is.
    """
    visible = _visible_chars(text)
    if len(visible) <= 1:
        return False
    if any(0xE000 <= ord(c) <= 0xF8FF for c in visible):
        return False  # Private Use Area glyph — icon font
    if _is_symbol_font(font_name):
        return False
    return any(unicodedata.category(c)[0] == "L" for c in text)


def _table_cell_rects(page: fitz.Page) -> list[fitz.Rect]:
    """Return a flat list of table cell bounding boxes found on this page.

    Uses PyMuPDF's find_tables() (available since 1.23).  Returns an empty
    list if no tables are found or if the call fails.
    """
    try:
        finder = page.find_tables()
        cells: list[fitz.Rect] = []
        for tab in finder.tables:
            for cell in tab.cells:
                if cell is not None:
                    cells.append(fitz.Rect(cell))
        return cells
    except Exception:
        return []


def _merge_same_line_blocks(blocks: list[tuple]) -> list[tuple]:
    """
    Merge blocks on the same visual line (overlapping y-range, adjacent x)
    into a single block.

    Reduces API call count for PDFs that store each word or phrase as a
    separate positioned text object (common in InDesign/DTP-produced PDFs).

    Merge criteria — both must be true:
    - Same line: y-centroids within avg_height * 0.8 of each other.
    - Adjacent:  horizontal gap ≤ avg_height * 1.5 (roughly 1–1.5 word spaces).
    - Table cells are never merged.

    The merged block inherits the union bbox and joined text; font metadata
    (fontsize, orig_font_name, flags) is taken from the leftmost fragment.
    """
    if len(blocks) <= 1:
        return blocks

    # Sort top-to-bottom then left-to-right.  Round y-centre to the nearest
    # 4 pts so minor baseline wobble doesn't split same-line blocks.
    sorted_blks = sorted(
        blocks,
        key=lambda b: (round((b[1] + b[3]) / 2 / 4) * 4, b[0]),
    )

    merged: list[tuple] = []
    cur = list(sorted_blks[0])  # mutable working copy

    for blk in sorted_blks[1:]:
        cx0, cy0, cx1, cy1 = cur[0], cur[1], cur[2], cur[3]
        bx0, by0, bx1, by1 = blk[0], blk[1], blk[2], blk[3]

        # Never merge table cells.
        if cur[8] or blk[8]:
            merged.append(tuple(cur))
            cur = list(blk)
            continue

        cy_mid = (cy0 + cy1) / 2
        by_mid = (by0 + by1) / 2
        avg_h  = ((cy1 - cy0) + (by1 - by0)) / 2
        h_gap  = bx0 - cx1

        same_line = abs(cy_mid - by_mid) < avg_h * 0.8
        nearby    = h_gap <= avg_h * 1.5

        if same_line and nearby:
            # Add a space between fragments unless the gap is tiny (e.g.
            # hyphen touching the adjacent word) or current text already ends
            # with whitespace.
            sep = ""
            if h_gap > 2 and not cur[4].endswith((" ", "\n")):
                sep = " "
            cur[0] = min(cx0, bx0)
            cur[1] = min(cy0, by0)
            cur[2] = max(cx1, bx1)
            cur[3] = max(cy1, by1)
            cur[4] = cur[4].rstrip() + sep + blk[4].lstrip()
            # fontsize, orig_font_name, flags, is_table_cell kept from cur
        else:
            merged.append(tuple(cur))
            cur = list(blk)

    merged.append(tuple(cur))
    return merged


def prescan_blocks(
    pdf_path: str,
    filter_icons: bool = True,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng",
    ocr_service: str = "Tesseract",
    ocr_config: dict | None = None,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
) -> tuple[list[list[tuple]], int, int, dict[str, bytes]]:
    """
    Scan the PDF, collect translatable text blocks, and extract embedded fonts.
    Returns (page_blocks, total_blocks, n_pages, embedded_fonts).

    Block tuple: (x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell)
    Vertical/rotated blocks are skipped.
    is_table_cell is True when detect_tables=True and the block's centre falls
    inside a table cell detected by PyMuPDF's find_tables().  When
    detect_tables=False, all blocks are treated as regular text (is_table_cell
    is always False) and the write step applies right/down-expand rather than
    shrink-to-fit.

    When ocr_fallback is True (default), pages that yield zero text blocks and
    are detected as scanned (no usable text layer) are processed with OCR.

    When force_ocr is True, the text layer is ignored entirely and every page
    is rendered as an image and processed with OCR.  Use this for mixed PDFs
    where the text layer is incomplete or misleading (e.g. DTP PDFs where
    scanned and digital content are interleaved).

    ocr_service selects the OCR engine for both fallback and force_ocr modes:
      "Tesseract" — use pytesseract (requires pytesseract + Pillow)
      "Ollama"    — use a vision LLM via Ollama; ocr_config must contain
                    {"url": str, "model": str, "prompt": str}
    """
    doc = fitz.open(pdf_path)
    n_pages        = len(doc)
    embedded_fonts = extract_doc_fonts(doc)
    page_blocks: list[list[tuple]] = []
    total_blocks = 0

    for page in doc:
        if force_ocr:
            # Bypass the text layer entirely — render page as image and OCR it.
            if ocr_service == "Ollama":
                from ocr_utils import ocr_page_llm  # lazy
                cfg = ocr_config or {}
                blocks = ocr_page_llm(
                    page,
                    url=cfg.get("url", "http://localhost:11434"),
                    model=cfg.get("model", "glm-ocr"),
                    prompt=cfg.get("prompt", ""),
                )
            else:
                from ocr_utils import ocr_page  # lazy
                blocks = ocr_page(page, lang=ocr_lang)
        else:
            cell_rects = _table_cell_rects(page) if detect_tables else []
            blocks: list[tuple] = []
            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                lines = blk.get("lines", [])
                if not lines:
                    continue
                if not all(_is_horizontal(ln) for ln in lines):
                    continue

                # Detect multi-column same-Y layout: two or more lines within the
                # same block that share the same Y-band indicate side-by-side
                # columns (e.g. "Label:   Value" stored as two positioned objects).
                # Group lines by Y-band so they can be emitted as separate blocks.
                y_groups: list[list[dict]] = []
                for ln in lines:
                    ly_mid = (ln["bbox"][1] + ln["bbox"][3]) / 2
                    lh     = max(ln["bbox"][3] - ln["bbox"][1], 1.0)
                    for grp in y_groups:
                        g_mid = (grp[0]["bbox"][1] + grp[0]["bbox"][3]) / 2
                        if abs(ly_mid - g_mid) < lh * 0.4:
                            grp.append(ln)
                            break
                    else:
                        y_groups.append([ln])

                has_multicol = any(len(g) > 1 for g in y_groups)

                if has_multicol:
                    # Multi-column block: emit each line as its own block using
                    # the line's own bbox, so translated text lands at the
                    # correct column position rather than stacking at x0.
                    # When filter_icons=True, single-char lines are still dropped.
                    for grp in y_groups:
                        for ln in grp:
                            ln_text = "".join(sp["text"] for sp in ln.get("spans", []))
                            if filter_icons and len(_visible_chars(ln_text)) <= 1:
                                continue
                            ln_text = ln_text.strip()
                            if not ln_text:
                                continue
                            first_span = ln["spans"][0] if ln.get("spans") else {}
                            orig_font_name = first_span.get("font", "")
                            skipped = not _has_translatable_text(ln_text, orig_font_name)
                            if DEBUG_PRESCAN:
                                status = "SKIP" if skipped else "keep"
                                print(f"[prescan] p{page.number+1} {status} (col)  "
                                      f"font={orig_font_name!r}  text={repr(ln_text[:40])}")
                            if skipped:
                                continue
                            fontsize = float(first_span.get("size", 11))
                            flags    = int(first_span.get("flags", 0))
                            lx0, ly0, lx1, ly1 = ln["bbox"]
                            cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
                            is_table_cell = any(r.contains(fitz.Point(cx, cy)) for r in cell_rects)
                            blocks.append((lx0, ly0, lx1, ly1, ln_text, fontsize, orig_font_name, flags, is_table_cell))
                else:
                    # Normal block: all lines at distinct Y positions — join with
                    # \n and treat as a single translatable unit (existing logic).
                    # When filter_icons=True (default), single-char lines are
                    # dropped — icon glyphs embedded in mixed blocks are removed
                    # so they don't reach the translation API.
                    text_lines: list[str] = []
                    for ln in lines:
                        ln_text = "".join(sp["text"] for sp in ln.get("spans", []))
                        if not filter_icons or len(_visible_chars(ln_text)) > 1:
                            text_lines.append(ln_text)

                    text = "\n".join(text_lines).strip()
                    if not text:
                        continue
                    first_span     = lines[0]["spans"][0] if lines[0].get("spans") else {}
                    orig_font_name = first_span.get("font", "")
                    skipped = not _has_translatable_text(text, orig_font_name)
                    if DEBUG_PRESCAN:
                        status = "SKIP" if skipped else "keep"
                        preview = repr(text[:40])
                        print(f"[prescan] p{page.number+1} {status}  font={orig_font_name!r}  text={preview}")
                    if skipped:
                        continue
                    fontsize      = float(first_span.get("size", 11))
                    flags: int    = int(first_span.get("flags", 0))
                    x0, y0, x1, y1 = blk["bbox"]
                    # Check if block centre falls inside a known table cell
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    is_table_cell = any(r.contains(fitz.Point(cx, cy)) for r in cell_rects)
                    blocks.append((x0, y0, x1, y1, text, fontsize, orig_font_name, flags, is_table_cell))
            # OCR fallback: if the page yielded no translatable blocks and looks
            # scanned, run OCR to extract text from the page image.
            if not blocks and ocr_fallback:
                from ocr_utils import is_scanned_page  # lazy — optional dep
                if is_scanned_page(page):
                    if ocr_service == "Ollama":
                        from ocr_utils import ocr_page_llm  # lazy
                        cfg = ocr_config or {}
                        blocks = ocr_page_llm(
                            page,
                            url=cfg.get("url", "http://localhost:11434"),
                            model=cfg.get("model", "glm-ocr"),
                            prompt=cfg.get("prompt", ""),
                        )
                    else:
                        from ocr_utils import ocr_page  # lazy
                        blocks = ocr_page(page, lang=ocr_lang)

        if merge_blocks and blocks:
            blocks = _merge_same_line_blocks(blocks)

        page_blocks.append(blocks)
        total_blocks += len(blocks)

    doc.close()
    return page_blocks, total_blocks, n_pages, embedded_fonts


# ---------------------------------------------------------------------------
# Translated PDF writer
# ---------------------------------------------------------------------------

def write_translated_pdf(
    orig_pdf_path: str,
    page_translations: list[list[tuple]],
    out_path: str,
    embedded_fonts: dict[str, bytes],
) -> None:
    """
    Write a translated PDF: image background + white rects + translated text.

    Block tuple: (x0, y0, x1, y1, translated_text, fontsize, orig_font_name, flags, is_table_cell)

    Text fitting strategy:
      Table cells (is_table_cell=True) — shrink-to-fit within orig_rect:
        Try font scales 1.0 → 0.85 → 0.70 → 0.55 → 0.40 (min 6 pt).
        No right/down expansion — preserves table layout.  Translation
        quality is secondary; the HTML output is the readable version.

      Regular blocks (is_table_cell=False) — expand, never shrink:
        1. Expand right to near page edge (original height).
        2. Original bounding rect.
        3. Expand downward at available width (×1.5 → ×5).
        4. insert_text fallback (overflows freely).
    """
    orig_doc = fitz.open(orig_pdf_path)
    out_doc  = fitz.open()

    for i, translations in enumerate(page_translations):
        orig_page = orig_doc[i]
        w = orig_page.rect.width
        h = orig_page.rect.height

        new_page = out_doc.new_page(width=w, height=h)
        bg_pix   = orig_page.get_pixmap(dpi=RENDER_DPI)
        new_page.insert_image(fitz.Rect(0, 0, w, h), pixmap=bg_pix)

        # Build per-page font registry
        page_font_registry: dict[tuple, tuple[str, str]] = {}
        for *_, fontsize, orig_font_name, flags, _is_tc in translations:
            key = (normalize_font_name(orig_font_name), flags)
            if key in page_font_registry:
                continue
            alias, font_bytes = resolve_font(orig_font_name, flags, embedded_fonts)
            tier = "base14"
            if font_bytes is not None:
                norm_check = normalize_font_name(orig_font_name)
                tier = "embedded" if norm_check in embedded_fonts else "liberation"
                try:
                    new_page.insert_font(fontname=alias, fontbuffer=font_bytes)
                except Exception:
                    # Registration failed — fall back to base14
                    is_bold, is_italic = _span_style(normalize_font_name(orig_font_name), flags)
                    fam   = _family_from_name(normalize_font_name(orig_font_name))
                    alias = _BASE14.get(fam, _BASE14["sans"]).get((is_bold, is_italic), "helv")
                    tier  = "base14(fallback)"
            page_font_registry[key] = (alias, tier)

        if DEBUG_FONTS:
            print(f"\n[pdf-translate] Page {i + 1} font registry:")
            for (norm, flg), (a, t) in page_font_registry.items():
                print(f"  '{norm}' flags={flg:#06x} → alias='{a}' tier={t}")

        def _clear(rect: fitz.Rect) -> None:
            s = new_page.new_shape()
            s.draw_rect(rect)
            s.finish(fill=(1, 1, 1), color=(1, 1, 1))
            s.commit()

        # Render each block
        for x0, y0, x1, y1, translated, fontsize, orig_font_name, flags, is_table_cell in translations:
            # Safety net: if this block was not filtered at prescan time but
            # the translated text is still icon/symbol content, skip it
            # entirely.  No white rect is drawn so the background image
            # (original page render) shows through with the original glyph.
            if not _has_translatable_text(translated, orig_font_name):
                continue

            key      = (normalize_font_name(orig_font_name), flags)
            alias, _ = page_font_registry[key]
            orig_rect = fitz.Rect(x0, y0, x1, y1)
            block_h   = y1 - y0

            if is_table_cell:
                # ── Table cell: shrink-to-fit within original bbox ──────────
                # No right/down expansion — preserves adjacent cell content.
                # Font shrinks in steps; minimum 6 pt.
                _clear(orig_rect)
                inserted = False
                for scale in (1.0, 0.85, 0.70, 0.55, 0.40):
                    try_size = max(round(fontsize * scale, 1), 6.0)
                    rc = new_page.insert_textbox(
                        orig_rect, translated,
                        fontsize=try_size, fontname=alias, color=(0, 0, 0), align=0,
                    )
                    if rc >= 0:
                        inserted = True
                        break
                if not inserted:
                    new_page.insert_text(
                        (x0, y0 + 6.0), translated,
                        fontsize=6.0, fontname=alias, color=(0, 0, 0),
                    )

            else:
                # ── Regular block: expand right/down, never shrink ──────────
                right_rect      = fitz.Rect(x0, y0, w - RIGHT_MARGIN, y1)
                has_right_space = right_rect.width > orig_rect.width
                clear_rect      = right_rect if has_right_space else orig_rect
                _clear(clear_rect)

                inserted = False

                if has_right_space:
                    rc = new_page.insert_textbox(
                        right_rect, translated,
                        fontsize=fontsize, fontname=alias, color=(0, 0, 0), align=0,
                    )
                    if rc >= 0:
                        inserted = True

                if not inserted:
                    rc = new_page.insert_textbox(
                        orig_rect, translated,
                        fontsize=fontsize, fontname=alias, color=(0, 0, 0), align=0,
                    )
                    if rc >= 0:
                        inserted = True

                if not inserted:
                    # Down-expand: use right-expanded width where available.
                    # Clear each candidate area before inserting so text doesn't
                    # render over the image background below y1.
                    expand_x1 = (w - RIGHT_MARGIN) if has_right_space else x1
                    for mult in (1.5, 2.0, 3.0, 5.0):
                        try_rect = fitz.Rect(x0, y0, expand_x1, y0 + block_h * mult)
                        _clear(try_rect)
                        rc = new_page.insert_textbox(
                            try_rect, translated,
                            fontsize=fontsize, fontname=alias, color=(0, 0, 0), align=0,
                        )
                        if rc >= 0:
                            inserted = True
                            break

                if not inserted:
                    new_page.insert_text(
                        (x0, y0 + fontsize), translated,
                        fontsize=fontsize, fontname=alias, color=(0, 0, 0),
                    )

    orig_doc.close()
    out_doc.save(out_path)
    out_doc.close()


# ---------------------------------------------------------------------------
# Preview and output utilities
# ---------------------------------------------------------------------------

def pdf_to_image(pdf_path: str, page_idx: int = 0) -> str:
    """Render one page of a PDF to a PNG temp file. Returns the file path."""
    doc       = fitz.open(pdf_path)
    page_idx  = max(0, min(page_idx, len(doc) - 1))
    pix       = doc[page_idx].get_pixmap(dpi=PREVIEW_DPI)
    # Close handle before PyMuPDF writes — Windows holds an exclusive lock
    # on NamedTemporaryFile until it is closed.
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    pix.save(tmp.name)
    doc.close()
    return tmp.name


def make_side_by_side_pdf(
    original_path: str,
    translated_path: str,
    output_path: str | None = None,
) -> str:
    """
    Build a PDF where each page is the original (left) and translation (right).
    Portrait input → landscape output (2W × H).

    Both sides use show_pdf_page — preserves the selectable text layer on both.
    """
    orig_doc  = fitz.open(original_path)
    trans_doc = fitz.open(translated_path)
    out_doc   = fitz.open()

    for i in range(len(orig_doc)):
        w = orig_doc[i].rect.width
        h = orig_doc[i].rect.height
        new_page = out_doc.new_page(width=2 * w, height=h)
        new_page.show_pdf_page(fitz.Rect(0, 0, w, h), orig_doc, i)
        if i < len(trans_doc):
            new_page.show_pdf_page(fitz.Rect(w, 0, 2 * w, h), trans_doc, i)
        else:
            new_page.show_pdf_page(fitz.Rect(w, 0, 2 * w, h), orig_doc, i)

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        output_path = tmp.name

    out_doc.save(output_path)
    orig_doc.close()
    trans_doc.close()
    out_doc.close()
    return output_path


# ---------------------------------------------------------------------------
# Side-by-side HTML reading view
# ---------------------------------------------------------------------------


def generate_side_by_side_html(
    source: str,
    target: str,
    page_blocks: list[list[tuple]],
    page_translations: list[list[tuple]],
    stem: str = "document",
    meta: dict | None = None,
) -> str:
    """
    Build an HTML side-by-side reading view with a metadata header.
    Each block pair: original (left, dark) | translation (right, blue).

    meta (optional): {"service": "Ollama", "url": "http://...", "model": "gemma3:12b"}
    Block tuple: (x0, y0, x1, y1, text, ...)
    """

    def esc(t: str) -> str:
        return _html.escape(t).replace("\n", "<br>")

    title     = _html.escape(f"{stem} — {source} → {target}")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_pages   = len(page_blocks)
    total_blocks = sum(len(p) for p in page_blocks)

    svc_label = ""
    if meta:
        svc   = meta.get("service", "")
        model = meta.get("model", "")
        url   = meta.get("url", "")
        svc_label = f"{svc} ({model})" if model else svc
        if url:
            svc_label = f"{svc_label} — {url}" if svc_label else url

    meta_rows = [
        ("File",                _html.escape(stem)),
        ("Source language",     _html.escape(source)),
        ("Target language",     _html.escape(target)),
        ("Translation service", _html.escape(svc_label) if svc_label else "—"),
        ("Pages",               str(n_pages)),
        ("Text blocks",         str(total_blocks)),
        ("Translated",          timestamp),
    ]
    meta_html = "\n".join(
        f'    <tr><th>{k}</th><td>{v}</td></tr>' for k, v in meta_rows
    )

    rows: list[str] = []
    for p, (blocks, translations) in enumerate(zip(page_blocks, page_translations)):
        rows.append(f'<div class="page-header">Page {p + 1}</div>')
        rows.append('<div class="page-body">')
        for block, trans_block in zip(blocks, translations):
            orig_text  = esc(block[4].strip())
            trans_text = esc(trans_block[4].strip())
            rows.append(
                f'<div class="block-row">'
                f'<div class="orig">{orig_text}</div>'
                f'<div class="trans">{trans_text}</div>'
                f'</div>'
            )
        rows.append("</div>")

    body = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: Georgia, serif; margin: 0; padding: 0;
            background: #fafafa; color: #222; line-height: 1.6; }}
    .site-header {{ font-family: sans-serif; padding: 0.8rem 2rem;
                    background: #1a1a2e; color: #eee;
                    font-size: 1rem; font-weight: bold; }}
    .meta {{ background: #fff; border-bottom: 2px solid #ddd;
             padding: 0.75rem 2rem; font-family: sans-serif; font-size: 0.85rem; }}
    .meta table {{ border-collapse: collapse; }}
    .meta th {{ text-align: left; color: #666; font-weight: normal;
                padding: 0.15rem 1.5rem 0.15rem 0; white-space: nowrap; }}
    .meta td {{ color: #222; }}
    .page-header {{ font-family: sans-serif; font-size: 0.8rem;
                    text-transform: uppercase; letter-spacing: 0.07em;
                    color: #888; padding: 0.6rem 2rem;
                    background: #f0f0f0; border-top: 2px solid #ccc;
                    border-bottom: 1px solid #ddd; }}
    .page-body {{ padding: 0.25rem 2rem 0.5rem; }}
    .block-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem;
                  padding: 0.5rem 0; border-bottom: 1px solid #eee; }}
    .orig  {{ color: #333; }}
    .trans {{ color: #1a5c9e; }}
  </style>
</head>
<body>
  <div class="site-header">{title}</div>
  <div class="meta">
    <table>
{meta_html}
    </table>
  </div>
{body}
</body>
</html>"""


