"""Error logging utility for desktop pet."""
import traceback
import os
from pathlib import Path
from datetime import datetime

_LOG_PATH = None
_MAX_LINES = 500
_ENABLED = True


def set_log_path(path: str | Path):
    global _LOG_PATH
    _LOG_PATH = Path(path)


def set_log_enabled(enabled: bool):
    global _ENABLED
    _ENABLED = enabled


def _get_log_path():
    if _LOG_PATH is not None:
        return _LOG_PATH
    # fallback: project root
    return Path(__file__).parent / "pet-log.txt"


def log_error(msg, exc_info=False):
    if not _ENABLED:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    if exc_info:
        entry += f"\n{traceback.format_exc()}"
    entry += "\n"

    log_path = _get_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except:
        pass

    # Rotate if too long
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LINES:
            log_path.write_text("\n".join(lines[-_MAX_LINES // 2:]),
                                encoding="utf-8")
    except:
        pass


def log_startup():
    log_error("Pet started")


def log_shutdown():
    log_error("Pet stopped")
