# 📖 pdf-translate — User Guide

A self-hosted web app for translating PDF documents. Upload a PDF, choose your languages and backend, and download the translated output.

---

## 🚀 Starting the app

```bash
# Activate the virtual environment (first time or new terminal)
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux / macOS

python app.py
```

Open **`http://localhost:7860`** in your browser.

---

## 🌐 Translating a document

### 1. Upload a PDF

Click **PDF file** and select a file, or drag and drop it onto the upload area. The first page renders in the preview panel on the right.

### 2. Set languages

- **Source language** — the language of the original document. Set to *Auto-detect* if unsure; most backends handle this correctly.
- **Target language** — the language you want the output in.

### 3. Choose a translation backend

| Backend | Requires | Best for |
|---------|----------|---------|
| **Google** | Internet access | Quick, no setup, zero-config fallback |
| **Ollama** | Ollama running on LAN (configured URL + model) | Offline / private documents |
| **LibreTranslate** | LibreTranslate running on LAN (configured URL) | Offline, open-source alternative |

The active backend is shown in the dropdown. Backend URLs and models are configured in the **Backend settings** accordion at the bottom of the controls panel.

### 4. Set options

**Allow text reflow (collapse line breaks)**
Off by default. When on, consecutive lines within a text block are joined with a space before being sent to the translation backend. This helps when the original PDF has hard line breaks inside paragraphs (common in scanned or older PDFs), so the translator sees complete sentences rather than fragments.

Keep this off when the line breaks are meaningful — addresses, bullet lists, tables, code blocks.

**Filter icon/symbol glyphs (default on)**
On by default. Strips single-character lines that are likely icon glyphs (phone symbols, arrows, email icons, decorators) from mixed text blocks before translation. Icon fonts map glyph codepoints to regular Unicode letters, so without filtering they reach the translator and come back as random letters in the output.

Uncheck this only if you notice legitimate single-character content (e.g. section letters, numbered items) being dropped from the translation.

**Merge split lines (default off)**
Off by default. Some PDFs — typically produced by desktop publishing tools such as InDesign — store each word or phrase as a separate positioned text object instead of grouping them into paragraph blocks. This results in many small API calls per page (50–80+ for a dense document), which can trigger rate-limit errors (HTTP 429) on self-hosted backends such as LibreTranslate.

When enabled, fragments on the same visual line that are horizontally adjacent are merged into a single text block before translation, reducing API call count by 5–10× on affected PDFs.

Leave this off for most documents — PDFs from Word, LibreOffice, or LaTeX already have proper paragraph-level blocks and are unaffected. Enable it when you see repeated 429 errors, or when the debug log shows many small single-line blocks per page (e.g. `p2:77`).

**Caution for multi-column layouts** — if two columns share the same y-position and the gap between them is narrow, adjacent columns on the same row may be incorrectly merged into one block.

**Force OCR — ignore text layer (default off)**
Off by default. When enabled, the existing text layer is ignored entirely and every page is rendered as a high-resolution image and processed with OCR (using the engine selected in *OCR engine*). Use this for mixed PDFs where digital text and scanned content are interleaved — in these documents, translating only the text layer produces a partial and misleading result.

Implications:
- Requires the OCR engine to be installed and reachable (Tesseract or Ollama vision model on the configured URL).
- Slower than text-layer translation — OCR adds per-page image rendering time.
- All blocks get approximate bounding boxes from OCR; layout fidelity is lower than text-layer extraction.
- `merge_blocks` has no effect when `force_ocr` is on, since OCR already produces paragraph-level blocks.

**Detect table cells (shrink-to-fit) (default on)**
On by default. When enabled, PyMuPDF's table detector scans each page for table structures and marks any text block whose centre falls inside a table cell. Translated text in table cells is fitted using shrink-to-fit: the font is scaled down in steps (100% → 85% → 70% → 55% → 40%, minimum 6 pt) until the text fits within the original cell boundary.

Disable this if you see translated text that is abnormally small or illegible — this indicates a false-positive table detection, where a bordered text box, sidebar, or ruled section was misidentified as a table. With table detection off, all blocks use the standard right/down-expand fitting instead.

### 5. Translate

Click **Translate**. The status box shows progress block by block. Translation time depends on document length and backend speed — from a few seconds (Google, short doc) to several minutes (Ollama, long doc).

Do not navigate away or close the tab while translating.

### 6. Download outputs

Three downloads appear when translation completes:

| File | Description |
|------|-------------|
| **Translated PDF** | The full translated document. Text is placed at the original position; images and layout are preserved as a background. Text layer is selectable and searchable. |
| **Side-by-side PDF** | Landscape format: original page on the left, translated page on the right, layout-matched. Useful for visual comparison. Both sides have a selectable text layer. |
| **Reading PDF** | Landscape format: original text on the left, translated text on the right, clean and reflowable — not constrained to the original layout. Useful when the translated PDF text is too small to read comfortably. Can be appended to the side-by-side PDF with any PDF merger. |

### 7. Page navigation

After translation, use the **◀ Prev** and **Next ▶** buttons to flip through pages side by side (original left, translated right).

---

## 🔧 Backend configuration

Open the **Backend settings** accordion in the controls panel.

### Ollama

| Field | Description |
|-------|-------------|
| URL | Base URL of the Ollama instance, e.g. `http://your-server-ip:11434` |
| Model | Model name, e.g. `translategemma:latest` |
| System prompt | The translation prompt sent to the model. `{source_lang}`, `{target_lang}`, and `{text}` are substituted at runtime. |
| API key | Optional Bearer token. Leave blank if Ollama has no authentication. |

Click **Test connection** to verify reachability and see the list of available models.

### LibreTranslate

| Field | Description |
|-------|-------------|
| URL | Base URL, e.g. `http://your-server-ip:5000` |
| API key | Optional. Required only if your LibreTranslate instance has `--api-keys` enabled. |

Click **Test connection** to verify and see the number of available language pairs.

### Google Translate

Uses the unofficial `translate.googleapis.com` endpoint — no API key or account required. Requires internet access. Not rate-limit-tested for large documents; use Ollama or LibreTranslate for high-volume or offline use.

### Saving configuration

Click **Save configuration** at the bottom of the Backend settings accordion. Settings are written to `config.json` next to `app.py` and loaded on next start. `config.json` is gitignored — it is never committed.

---

## 🌍 Supported languages

| Code | Language |
|------|----------|
| `auto` | Auto-detect (source only) |
| `en` | English |
| `nl` | Nederlands |
| `de` | Deutsch |
| `fr` | Français |
| `es` | Español |
| `it` | Italiano |
| `pt` | Português |
| `ru` | Русский |
| `ja` | 日本語 |
| `zh` | 中文 |
| `ko` | 한국어 |
| `ar` | العربية |
| `tr` | Türkçe |
| `pl` | Polski |
| `tl` | Filipino |

Auto-detect is only available as a source language. All codes can be used as the target.

---

## ⚠️ Known limitations

- **Scanned PDFs** — Image-only PDFs have no text layer by default. Enable **Force OCR** to OCR every page with Tesseract or an Ollama vision model (no external preprocessing needed).
- **Mathematical formulas** — Formula content is not preserved. Plain text blocks only. For academic/scientific PDFs with LaTeX, try [pdf2zh-next](https://github.com/pdf2zh/pdf2zh-next).
- **Vertical / rotated text** — Blocks with non-horizontal text direction are skipped.
- **Icon fonts** — Icon glyphs remapped to ASCII characters may slip through if the font name is not recognised. Enable *Filter icon/symbol glyphs* (default on) to catch single-character instances.
- **Large documents** — Translation time scales linearly with block count. For a 100-page document with Ollama, expect 5–15 minutes depending on model and hardware.

---

## 📚 Further reading

- [API reference](api.md) — REST API, timeouts, Paperless-ngx integration
- [Backends](backends.md) — Ollama and LibreTranslate setup
