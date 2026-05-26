"""
pdf-translate — LibreTranslate backend.

API docs: https://libretranslate.com/docs/
Self-hosted: https://github.com/LibreTranslate/LibreTranslate
"""

import time

import httpx

from config import CONNECTION_TIMEOUT, TRANSLATE_TIMEOUT
from exceptions import RateLimitError

_RETRY_DELAYS = (2.0, 4.0, 8.0)  # seconds between 429 retries (3 retries total)


def test_connection(url: str) -> str:
    """Check reachability via GET /languages (no API key required)."""
    try:
        r = httpx.get(
            f"{url.rstrip('/')}/languages",
            timeout=CONNECTION_TIMEOUT,
        )
        r.raise_for_status()
        return f"✓ Connected — {len(r.json())} language(s) available"
    except httpx.ConnectError:
        return "✗ Connection refused — is LibreTranslate running at that URL?"
    except httpx.TimeoutException:
        return f"✗ Timed out after {CONNECTION_TIMEOUT}s"
    except Exception as e:
        return f"✗ {e}"


def call(text: str, source: str, target: str, url: str, key: str) -> str:
    """Translate one text block via POST /translate.

    Retries up to 3 times with exponential backoff (2 s → 4 s → 8 s) on HTTP
    429 Too Many Requests.  All other HTTP errors are raised immediately.
    """
    payload: dict = {"q": text, "source": source, "target": target}
    if key:
        payload["api_key"] = key

    endpoint = f"{url.rstrip('/')}/translate"
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        r = httpx.post(endpoint, json=payload, timeout=TRANSLATE_TIMEOUT)
        if r.status_code != 429 or delay is None:
            if r.status_code == 429:
                raise RateLimitError(
                    f"LibreTranslate rate limit exceeded after {len(_RETRY_DELAYS)} retries."
                )
            r.raise_for_status()
            return r.json()["translatedText"]
        time.sleep(delay)

    # Unreachable — loop always returns or raises above.
    raise RuntimeError("libretranslate.call: unexpected exit from retry loop")


