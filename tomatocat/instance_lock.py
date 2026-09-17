"""Cross-process workspace lock for the primary TomatoCat runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class WorkspaceInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file: BinaryIO | None = None
        try:
            file = self.path.open("a+b")
            file.seek(0)
            if file.read(1) == b"":
                file.write(b"0")
                file.flush()
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if file is not None:
                file.close()
            raise RuntimeError(f"another TomatoCat instance is using workspace {self.path.parent}") from exc
        assert file is not None
        self._file = file

    def release(self) -> None:
        file = self._file
        if file is None:
            return
        try:
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()
            self._file = None
