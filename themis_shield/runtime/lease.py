"""Non-blocking, per-user interprocess ownership of a selected GPU scheduler.

The file lock is released by the OS on process death. This is not protection
against other users, other GPU software or same-user malware.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..inference import TargetUnavailable


class DeviceLease:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        self._file = os.fdopen(fd, "r+b", buffering=0)
        try:
            if os.fstat(fd).st_size == 0:
                self._file.write(b"0")
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            raise TargetUnavailable("local GPU scheduler already owned") from None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        fd = self._file.fileno()
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        self._file.close()
        self._closed = True
