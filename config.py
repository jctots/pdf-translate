"""
pdf-translate — configuration constants and persistence.
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# HTTP timeouts
# ---------------------------------------------------------------------------

CONNECTION_TIMEOUT = 5.0   # seconds — for connection-test calls
TRANSLATE_TIMEOUT  = 30.0  # seconds — for translation calls

# ---------------------------------------------------------------------------
# Language definitions
# ---------------------------------------------------------------------------

SOURCE_LANGUAGES = [
    ("Auto-detect", "auto"),
    ("English",     "en"),
    ("Nederlands",  "nl"),
    ("Deutsch",     "de"),
    ("Français",    "fr"),
    ("Español",     "es"),
    ("Italiano",    "it"),
    ("Português",   "pt"),
    ("Русский",     "ru"),
    ("日本語",       "ja"),
    ("中文",         "zh"),
    ("한국어",        "ko"),
    ("العربية",      "ar"),
    ("Türkçe",      "tr"),
    ("Polski",      "pl"),
    ("Filipino",    "tl"),
]

TARGET_LANGUAGES = SOURCE_LANGUAGES[1:]  # no Auto-detect

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_URL   = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "translategemma:latest"
# Can be overridden by LIBRETRANSLATE_URL env var (set in docker-compose when
# the bundled LibreTranslate service is used).
LIBRE_DEFAULT_URL    = os.environ.get("LIBRETRANSLATE_URL", "http://localhost:5000")
# Can be overridden by PDF_TRANSLATE_BACKEND env var.
# Bare Python install: "Google" (no setup needed).
# Docker with bundled LibreTranslate: set to "LibreTranslate" in docker-compose.
DEFAULT_BACKEND      = os.environ.get("PDF_TRANSLATE_BACKEND", "Google")

# OCR defaults — kept in sync with ocr_utils constants (duplicated here to
# avoid importing ocr_utils at config load time, which would pull in fitz).
OCR_DEFAULT_SERVICE      = "Tesseract"
OCR_LLM_DEFAULT_MODEL    = "glm-ocr"
OCR_LLM_DEFAULT_PROMPT   = (
    "Extract all text from this document image. "
    "Return only the text content, preserving paragraph structure with blank lines "
    "between paragraphs. Do not add explanations, formatting, or markdown."
)

OLLAMA_DEFAULT_SYSTEM_PROMPT = (
    "You are a professional {source_lang} to {target_lang} translator. "
    "Your goal is to accurately convey the meaning and nuances of the original "
    "{source_lang} text while adhering to {target_lang} grammar, vocabulary, "
    "and cultural sensitivities.\n"
    "Produce only the {target_lang} translation, without any additional "
    "explanations or commentary. Please translate the following {source_lang} "
    "text into {target_lang}:\n\n\n"
    "{text}"
)

CONFIG_PATH = Path(__file__).parent / "data" / "config.json"

DEFAULT_CONFIG: dict = {
    "backend":              DEFAULT_BACKEND,
    "source":               "auto",
    "target":               "en",
    "allow_wrap":           False,
    "filter_icons":         True,
    "merge_blocks":         False,
    "detect_tables":        True,
    "force_ocr":            False,
    "ollama_url":           OLLAMA_DEFAULT_URL,
    "ollama_model":         OLLAMA_DEFAULT_MODEL,
    "ollama_system_prompt": OLLAMA_DEFAULT_SYSTEM_PROMPT,
    "ollama_key":           "",
    "libre_url":            LIBRE_DEFAULT_URL,
    "libre_key":            "",
    # OCR settings
    "ocr_service":          OCR_DEFAULT_SERVICE,
    "ocr_ollama_model":     OCR_LLM_DEFAULT_MODEL,
    "ocr_ollama_prompt":    OCR_LLM_DEFAULT_PROMPT,
}

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open() as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception as exc:
            print(f"[pdf-translate] WARNING: failed to load {CONFIG_PATH}: {exc} — using defaults", flush=True)
    return dict(DEFAULT_CONFIG)


def save_config(
    backend: str,
    source: str,
    target: str,
    allow_wrap: bool,
    filter_icons: bool,
    ollama_url: str,
    ollama_model: str,
    ollama_system_prompt: str,
    ollama_key: str,
    libre_url: str,
    libre_key: str,
    ocr_service: str = OCR_DEFAULT_SERVICE,
    ocr_ollama_model: str = OCR_LLM_DEFAULT_MODEL,
    ocr_ollama_prompt: str = OCR_LLM_DEFAULT_PROMPT,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    force_ocr: bool = False,
) -> str:
    cfg = {
        "backend":              backend,
        "source":               source,
        "target":               target,
        "allow_wrap":           allow_wrap,
        "filter_icons":         filter_icons,
        "merge_blocks":         merge_blocks,
        "detect_tables":        detect_tables,
        "force_ocr":            force_ocr,
        "ollama_url":           ollama_url,
        "ollama_model":         ollama_model,
        "ollama_system_prompt": ollama_system_prompt,
        "ollama_key":           ollama_key,
        "libre_url":            libre_url,
        "libre_key":            libre_key,
        "ocr_service":          ocr_service,
        "ocr_ollama_model":     ocr_ollama_model,
        "ocr_ollama_prompt":    ocr_ollama_prompt,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
    return "✓ Saved."


def update_config(updates: dict) -> None:
    """Merge *updates* into the persisted config and write back to config.json.

    Only keys present in *updates* are changed; all other values are preserved.
    """
    merged = {**load_config(), **updates}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as f:
        json.dump(merged, f, indent=2)
