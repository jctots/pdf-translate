"""
pdf-translate — Ollama backend.

Uses POST /api/chat with a configurable system prompt.
Tested with translategemma:latest.
API docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import httpx

from config import CONNECTION_TIMEOUT, TRANSLATE_TIMEOUT


def test_connection(url: str, key: str) -> str:
    """Check reachability via GET /api/tags."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = httpx.get(
            f"{url.rstrip('/')}/api/tags",
            headers=headers,
            timeout=CONNECTION_TIMEOUT,
        )
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return f"✓ Connected — {len(models)} model(s) available"
    except httpx.ConnectError:
        return "✗ Connection refused — is Ollama running at that URL?"
    except httpx.TimeoutException:
        return f"✗ Timed out after {CONNECTION_TIMEOUT}s"
    except Exception as e:
        return f"✗ {e}"


def call(
    text: str,
    source: str,
    target: str,
    url: str,
    model: str,
    system_prompt: str,
    key: str,
) -> str:
    """Translate one text block via POST /api/chat."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # Use .replace() instead of .format() — PDF text may contain { } characters
    prompt = (
        system_prompt
        .replace("{source_lang}", source)
        .replace("{target_lang}", target)
        .replace("{text}", text)
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    r = httpx.post(
        f"{url.rstrip('/')}/api/chat",
        json=payload,
        headers=headers,
        timeout=TRANSLATE_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


