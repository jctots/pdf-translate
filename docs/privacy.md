# Privacy

pdf-translate is designed to keep your documents on your infrastructure. This page explains exactly what each backend does and how to verify it.

## What each backend does with your text

| Backend | Where your text goes | Private? |
|---|---|---|
| **Ollama** | The URL you configure (default: `http://localhost:11434`) | ✅ Your server |
| **LibreTranslate** | The URL you configure (default: `http://localhost:5000`) | ✅ Your server |
| **Google Translate** | `translate.googleapis.com` (Google's servers) | ❌ External |

The Google backend is a zero-config fallback for quick local testing. It is labelled "Cloud" in the UI and the backend comparison table — do not use it for sensitive documents.

## What data is sent

For Ollama and LibreTranslate: **only the text blocks extracted from the PDF** — no filename, no metadata, no raw PDF bytes. Text is sent per block to the configured server URL and nowhere else.

For Google Translate: text blocks are sent to `translate.googleapis.com` in plain HTTP GET requests (Google's public translate API).

## Paperless-ngx webhook data flow

When using the webhook container, the full chain is:

```
Paperless-ngx → webhook container → pdf-translate → translation backend
```

- The webhook receives a document notification from Paperless (on your LAN)
- It downloads the PDF from your Paperless instance (LAN)
- It calls pdf-translate, which extracts text and forwards it to the configured backend (Ollama or LibreTranslate on your LAN)
- The translated companion PDF is uploaded back to Paperless (LAN)

At no point does the webhook container contact any external service. The privacy guarantee is the same as the main app — determined entirely by which translation backend is configured.

## Verify it yourself

### Option 1 — tcpdump (Linux/macOS)

Capture all outbound traffic while translating a document. Replace `YOUR_LAN_IP` with the IP of your Ollama or LibreTranslate server.

```bash
sudo tcpdump -i any -n \
  'not host YOUR_LAN_IP and not port 22 and not port 53' \
  -w capture.pcap
```

Translate a document with the Ollama or LibreTranslate backend, then stop the capture. Open `capture.pcap` in Wireshark — if there are no external connections, nothing left your network.

### Option 2 — Docker network isolation

Run pdf-translate in a Docker network with `internal: true`. Docker blocks all external traffic on that network. If translation completes, it demonstrably cannot have reached the internet.

```yaml
networks:
  internal:
    internal: true

services:
  pdf-translate:
    image: ghcr.io/jctots/pdf-translate:latest
    networks: [internal]
    ports:
      - "7860:7860"
  libretranslate:
    image: libretranslate/libretranslate:latest
    networks: [internal]
```

### Option 3 — Read the source

The backends are short and self-contained:

- [`backends/ollama.py`](../backends/ollama.py) — ~65 lines; one `httpx.post` to `{url}/api/chat`
- [`backends/libretranslate.py`](../backends/libretranslate.py) — ~80 lines; one `httpx.post` to `{url}/translate`
- [`backends/google.py`](../backends/google.py) — ~30 lines; one `httpx.get` to `translate.googleapis.com`

No telemetry, no analytics, no crash reporting. The app does not call home.

## Securing the API

The REST API (`POST /api/translate`, `GET /api/config`, etc.) has no authentication by default. On localhost or a trusted private LAN this is acceptable. If you expose the service beyond a trusted network, protect it.

### Option A — API key (built-in)

Set the `PDF_TRANSLATE_API_KEY` environment variable. All API endpoints except `GET /api/health` will require an `Authorization: Bearer <key>` header. The Gradio UI is unaffected.

**Generate a key:**
```bash
openssl rand -hex 32
# example output: a3f8c1d2e4b5a6f7...
```

Or on Windows:
```powershell
[System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

**Set it in docker-compose.yml:**
```yaml
services:
  pdf-translate:
    environment:
      PDF_TRANSLATE_API_KEY: "your-key-here"
```

**Use it in curl:**
```bash
curl -X POST http://localhost:7860/api/translate \
  -H "Authorization: Bearer your-key-here" \
  -F "file=@document.pdf" \
  -F "source=de" -F "target=en" \
  --output translated.pdf
```

If `PDF_TRANSLATE_API_KEY` is not set, the API remains open (backward-compatible default).

### Option B — Reverse proxy with basic auth (protects UI too)

Put the service behind nginx, Caddy, or Authelia. This is the standard self-hosted approach and protects both the REST API and the Gradio UI.

Example Caddy config:
```
translate.yourdomain.com {
    basicauth {
        username <bcrypt-hash>
    }
    reverse_proxy localhost:7860
}
```

### Webhook container

The same pattern applies to the webhook container via `WEBHOOK_API_KEY`. If set, `POST /webhook` requires `Authorization: Bearer <key>`. Configure the matching header in your Paperless Workflow webhook settings.

```yaml
services:
  pdf-translate-webhook:
    environment:
      WEBHOOK_API_KEY: "your-webhook-key-here"
```
