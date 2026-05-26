"""
Unit tests for config.py — load, save, update.
"""

import json

import pytest
import config as cfg_module
from config import DEFAULT_CONFIG, OCR_LLM_DEFAULT_PROMPT, load_config, save_config, update_config


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, isolated_config):
        # isolated_config path does not exist yet
        result = load_config()
        assert result == DEFAULT_CONFIG

    def test_merges_partial_config(self, isolated_config):
        # Write only one key — remaining keys should come from DEFAULT_CONFIG
        isolated_config.write_text(json.dumps({"backend": "LibreTranslate"}))
        result = load_config()
        assert result["backend"] == "LibreTranslate"
        assert result["ollama_url"] == DEFAULT_CONFIG["ollama_url"]
        assert result["libre_url"] == DEFAULT_CONFIG["libre_url"]

    def test_full_config_overrides_all_defaults(self, isolated_config):
        custom = {
            "backend": "Ollama",
            "source": "nl",
            "target": "en",
            "allow_wrap": True,
            "filter_icons": False,
            "merge_blocks": True,
            "detect_tables": False,
            "force_ocr": True,
            "ollama_url": "http://10.0.0.1:11434",
            "ollama_model": "mymodel:latest",
            "ollama_system_prompt": "Translate: {text}",
            "ollama_key": "secret",
            "libre_url": "http://10.0.0.2:5000",
            "libre_key": "libresecret",
            "ocr_service": "Ollama",
            "ocr_ollama_model": "minicpm-v",
            "ocr_ollama_prompt": "Extract text.",
        }
        isolated_config.write_text(json.dumps(custom))
        result = load_config()
        assert result == custom

    def test_returns_defaults_on_invalid_json(self, isolated_config):
        isolated_config.write_text("not valid json {{")
        result = load_config()
        assert result == DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

_SAVE_ARGS = dict(
    backend="LibreTranslate",
    source="nl",
    target="en",
    allow_wrap=True,
    filter_icons=False,
    ollama_url="http://localhost:11434",
    ollama_model="translategemma:latest",
    ollama_system_prompt="Translate {text}",
    ollama_key="",
    libre_url="http://localhost:5000",
    libre_key="mykey",
    ocr_service="Tesseract",
    ocr_ollama_model="minicpm-v",
    ocr_ollama_prompt=OCR_LLM_DEFAULT_PROMPT,
)


class TestSaveConfig:
    def test_saves_and_loads_roundtrip(self, isolated_config):
        save_config(**_SAVE_ARGS)
        assert isolated_config.exists()
        result = load_config()
        assert result["backend"] == "LibreTranslate"
        assert result["source"] == "nl"
        assert result["target"] == "en"
        assert result["allow_wrap"] is True
        assert result["filter_icons"] is False
        assert result["libre_key"] == "mykey"

    def test_returns_confirmation_string(self, isolated_config):
        msg = save_config(**_SAVE_ARGS)
        assert "✓" in msg


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------

class TestUpdateConfig:
    def test_partial_update_preserves_other_keys(self, isolated_config):
        save_config(**_SAVE_ARGS)
        update_config({"backend": "Ollama"})
        result = load_config()
        assert result["backend"] == "Ollama"
        assert result["ollama_url"] == "http://localhost:11434"  # unchanged

    def test_multiple_keys_updated(self, isolated_config):
        save_config(**_SAVE_ARGS)
        update_config({"backend": "LibreTranslate", "libre_key": "newkey"})
        result = load_config()
        assert result["backend"] == "LibreTranslate"
        assert result["libre_key"] == "newkey"

    def test_update_on_missing_file_uses_defaults(self, isolated_config):
        # No file yet — update_config calls load_config which returns defaults
        update_config({"backend": "Ollama"})
        result = load_config()
        assert result["backend"] == "Ollama"
        assert result["ollama_url"] == DEFAULT_CONFIG["ollama_url"]
