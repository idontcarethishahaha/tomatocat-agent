"""Error logging utility for desktop pet."""
import traceback
from pathlib import Path
from datetime import datetime

LOG_PATH = Path(__file__).parent / "pet-log.txt"
MAX_LINES = 500


def log_error(msg, exc_info=False):
    """Append error to log file. Rotates at MAX_LINES lines."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    if exc_info:
        entry += f"\n{traceback.format_exc()}"
    entry += "\n"

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except:
        pass

    # Rotate if too long
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            LOG_PATH.write_text("\n".join(lines[-MAX_LINES // 2:]),
                                encoding="utf-8")
    except:
        pass


def log_startup():
    """Log application startup."""
    log_error("Pet started")


def log_shutdown():
    """Log application shutdown."""
    log_error("Pet stopped")
