# 🔧 Setting up translation backends

pdf-translate supports three translation backends. **LibreTranslate is bundled in `docker-compose.yml` by default** — it starts alongside pdf-translate with no extra configuration and keeps your documents fully private. **Ollama** is the alternative for LLM-quality translations. **Google Translate** works without any setup but sends your PDF text to Google's servers; use it only for quick tests or non-sensitive documents.

## 🦙 Ollama

Ollama runs open-source LLMs locally. Your documents never leave your machine or LAN.

→ [ollama.com](https://ollama.com) for downloads and the full model library.

### 🖥️ Option A — Local install (same machine as pdf-translate)

**1. Install Ollama**

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh
```

Windows: download from [ollama.com/download](https://ollama.com/download).

**2. Pull a translation model**

```bash
ollama pull translategemma:latest
```

Recommended models:

| Model | Size | Notes |
|-------|------|-------|
| `translategemma:latest` | ~4 GB | Good general-purpose translation; tested with pdf-translate |
| `qwen2.5:7b` | ~4 GB | Strong multilingual support |
| `mistral:7b` | ~4 GB | Good European language coverage |

**3. Configure pdf-translate**

In the UI, expand **Backend settings** and set:
- **Translation service** → `Ollama`
- **Ollama URL** → `http://localhost:11434`
- **Model** → `translategemma:latest`

Click **Test connection**, then **Save configuration**.

### 🐳 Option B — Docker Compose (Ollama + pdf-translate together)

```yaml
volumes:
  pdf_translate_data:
  ollama_data:

services:
  pdf-translate:
    image: ghcr.io/jctots/pdf-translate:latest
    ports:
      - "7860:7860"
    volumes:
      - pdf_translate_data:/app/data
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
    # Uncomment for NVIDIA GPU passthrough:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]
```

```bash
docker compose up -d

# Pull a model into the running container
docker exec -it <project>-ollama-1 ollama pull translategemma:latest
```

In the UI, set **Ollama URL** to `http://ollama:11434` (Docker internal hostname).

### ⚙️ OCR with Ollama vision models

When **Force OCR** is enabled, Ollama is used as a vision model to read scanned pages:

```bash
ollama pull glm-ocr   # 1.1 GB — purpose-built OCR, low VRAM
```

Set **OCR engine** → `Ollama (vision)` and **OCR model** → `glm-ocr` in Backend settings.

> `minicpm-v` (7.6 GB) is an alternative but requires significantly more VRAM. Stick with `glm-ocr` on machines with <8 GB VRAM.

**Performance reference:**

| Hardware | Expected speed |
|----------|---------------|
| CPU only | 1–5 min/page |
| Apple Silicon (M1/M2/M3) | 10–30 s/page (Metal) |
| NVIDIA GPU | 2–10 s/page |

## 🟩 LibreTranslate

LibreTranslate is a free, open-source translation API — fully self-hosted, no data leaves your network.

→ [libretranslate.com](https://libretranslate.com) · [GitHub](https://github.com/LibreTranslate/LibreTranslate)

### 🐳 Docker Compose (recommended)

```yaml
volumes:
  pdf_translate_data:
  libretranslate_data:

services:
  pdf-translate:
    image: ghcr.io/jctots/pdf-translate:latest
    ports:
      - "7860:7860"
    volumes:
      - pdf_translate_data:/app/data
    environment:
      # Delay between LibreTranslate API calls (ms). Prevents 429 on self-hosted instances.
      - LIBRETRANSLATE_BLOCK_DELAY_MS=200
    restart: unless-stopped

  libretranslate:
    image: libretranslate/libretranslate:latest
    ports:
      - "5000:5000"
    volumes:
      - libretranslate_data:/home/libretranslate/.local/share
    command: --req-limit 0 --load-only en,de,fr,es,it,nl,pt,ru,pl,ar,zh,ja,ko,tr
    restart: unless-stopped
```

**`--req-limit 0`** disables the per-minute request limit — important for long documents where pdf-translate makes many API calls (one per text block).

**`--load-only`** restricts which language models are downloaded. Remove it to load all ~50 languages (uses more disk and RAM). First startup takes a few minutes while models download.

```bash
docker compose up -d

# Monitor startup — wait for "Running on http://0.0.0.0:5000"
docker compose logs -f libretranslate
```

In the UI, set **LibreTranslate URL** to `http://libretranslate:5000` (Docker internal hostname). Click **Test connection**, then **Save configuration**.

### 🖥️ Local install

```bash
pip install libretranslate
libretranslate --req-limit 0
```

Set the URL to `http://localhost:5000` in pdf-translate.

### 🔑 API keys (optional)

By default, no key is required. To enforce one:

```
libretranslate --api-keys
```

Then set the key in **Backend settings → LibreTranslate API key**.

### ⚡ Rate limiting and throughput

| Setting | Default | Purpose |
|---------|---------|---------|
| `LIBRETRANSLATE_BLOCK_DELAY_MS` env var | `200` ms | Pause between API calls to stay under rate limit |
| **Merge split lines** checkbox | Off | Reduces block count 5–10× for DTP/InDesign PDFs |
| `--req-limit 0` in LT command | Not default | Disables per-minute limit on the LibreTranslate side |

### 🌐 Translation quality

LibreTranslate uses [Argos Translate](https://github.com/argosopentech/argos-translate) models:

- **Good:** European language pairs (EN ↔ DE, FR, ES, IT, NL, PT)
- **Moderate:** EN ↔ RU, PL, ZH, JA
- **Variable:** less common pairs via pivot translation through English

For best results, set **Source language** explicitly — avoid Auto-detect with LibreTranslate.

## 🟢 Google Translate (testing / fallback only)

No setup required. Uses the unofficial `translate.googleapis.com` endpoint — no API key, no account. Requires internet access.

**Limitations:** your PDF text is sent to Google's servers. Not suitable for confidential documents. No enforced file size limit in pdf-translate, but large documents may hit undocumented Google rate limits.

Use this backend for quick tests or when translating non-sensitive public documents. For anything private, use Ollama or LibreTranslate instead.
