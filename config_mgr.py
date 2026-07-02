"""Shared configuration management for desktop pet."""
import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    """Load config from config.json. Returns empty dict if not found."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg):
    """Save config to config.json."""
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8")


def get_api_key():
    """API key priority: env var DEEPSEEK_API_KEY > config.json."""
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if env_key:
        return env_key
    return load_config().get("api_key", "")


def get_model():
    """Get model name from config."""
    return load_config().get("model", "deepseek-chat")
