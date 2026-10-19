"""Tiny durability helpers: a cross-process file lock and atomic, fsynced JSON writes.

Why: the request counters and the response cache must survive restarts, crashes and two processes
touching the same files. A write goes to a temp file first, is flushed to disk, and then replaces the real
file in one step, so a crash can never leave a half-written file.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore
try:  # Windows
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore

_thread_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _thread_lock(key: str) -> threading.Lock:
    with _registry_lock:
        return _thread_locks.setdefault(key, threading.Lock())


@contextmanager
def locked(path: Path, timeout: float = 15.0) -> Iterator[None]:
    """Exclusive lock for ``path`` across threads AND processes. Do not nest calls for the same path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    tlock = _thread_lock(str(lock_path.resolve()))
    if not tlock.acquire(timeout=timeout):
        raise TimeoutError(f"Could not lock {path.name} (another thread holds it)")
    handle = None
    try:
        handle = open(lock_path, "a+b")
        if lock_path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Could not lock {path.name} (another process holds it)")
                    time.sleep(0.02)
        elif msvcrt is not None:  # pragma: no cover - Windows
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Could not lock {path.name} (another process holds it)")
                    time.sleep(0.02)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        if handle is not None:
            handle.close()
        tlock.release()


def write_json_atomic(path: Path, data: Any) -> None:
    """Write ``data`` as JSON: temp file -> flush -> fsync -> atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
