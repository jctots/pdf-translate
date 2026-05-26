"""
pdf-translate — typed exceptions.

Raised at the point of failure so callers can distinguish error categories
without string-matching exception messages.
"""


class OcrOomError(RuntimeError):
    """OCR model ran out of GPU memory (CUDA OOM)."""


class OcrModelError(RuntimeError):
    """OCR model hit an internal assertion error (e.g. GGML_ASSERT)."""


class RateLimitError(RuntimeError):
    """Translation backend returned 429 Too Many Requests after all retries."""


class BackendConnectionError(RuntimeError):
    """Translation backend is unreachable."""
