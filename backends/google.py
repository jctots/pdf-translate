"""
pdf-translate — Google Translate backend.

Uses the unofficial translate.googleapis.com endpoint — no API key required.
Not rate-limit-tested for document-sized workloads; use LibreTranslate or
Ollama for high-volume or offline use.
"""

import httpx

from config import TRANSLATE_TIMEOUT


def call(text: str, source: str, target: str) -> str:
    """Translate one text block via the Google Translate unofficial endpoint."""
    params = {
        "client": "gtx",
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }
    r = httpx.get(
        "https://translate.googleapis.com/translate_a/single",
        params=params,
        timeout=TRANSLATE_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()[0][0][0]


