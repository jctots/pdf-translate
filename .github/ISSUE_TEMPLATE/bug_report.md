---
name: Bug report
about: Something isn't working as expected
labels: bug
---

## Describe the bug

A clear description of what went wrong.

## Steps to reproduce

1. Upload PDF (type: digital text / scanned / DTP-InDesign / mixed)
2. Settings used (backend, source lang, target lang, checkboxes)
3. What happened

## Expected behaviour

What you expected instead.

## Environment

- **pdf-translate version:** (run `curl http://localhost:7860/api/health` and check `version`)
- **Backend:** Google / Ollama / LibreTranslate
- **Deployment:** Docker / Python local
- **OS:** Windows / Linux / macOS
- **Python version:** (if running locally)

## Logs / error output

Paste any error from the status box, docker logs, or `PDF_TRANSLATE_DEBUG=1` output.
