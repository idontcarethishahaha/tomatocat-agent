"""Shared configuration management for desktop pet."""
import json
import os
from pathlib import Path

_CONFIG_PATH = None


def set_config_path(path: str | Path):
    global _CONFIG_PATH
    _CONFIG_PATH = Path(path)


def _get_config_path():
    if _CONFIG_PATH is not None:
        return _CONFIG_PATH
    return Path(__file__).parent / "config.json"


def load_config():
    if _get_config_path().exists():
        try:
            return json.loads(_get_config_path().read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg):
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8")


def get_api_key():
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if env_key:
        return env_key
    return load_config().get("api_key", "")


def get_model():
    return load_config().get("model", "deepseek-chat")
