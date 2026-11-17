"""Crash-safe file writes.

A write either fully happens or not at all: data goes to a temporary file in the
same directory, is flushed and fsync'ed, then atomically renamed over the target
with ``os.replace``. On Windows the rename can transiently fail when antivirus or
a sync client (OneDrive, Dropbox) holds the target open, so it is retried.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

_REPLACE_RETRIES = 12
_REPLACE_BACKOFF_S = 0.05


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return  # Directory fsync is not supported on Windows; NTFS metadata is journaled.
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def replace_with_retry(src: Path, dst: Path) -> None:
    last_exc: OSError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:  # Windows sharing violation
            last_exc = exc
            time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".sf-", suffix=".tmp", dir=str(target.parent))  # short: Windows MAX_PATH
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(tmp, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def atomic_write_text(path: str | os.PathLike[str], text: str, encoding: str = "utf-8") -> Path:
    # newline normalisation: always write LF so content hashes are platform independent
    return atomic_write_bytes(path, text.replace("\r\n", "\n").encode(encoding))


def atomic_write_json(path: str | os.PathLike[str], obj: Any, *, indent: int = 2) -> Path:
    text = json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=False, default=str) + "\n"
    return atomic_write_text(path, text)


def atomic_write_yaml(path: str | os.PathLike[str], obj: Any) -> Path:
    text = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=100)
    return atomic_write_text(path, text)


def read_text(path: str | os.PathLike[str]) -> str:
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


def read_json(path: str | os.PathLike[str]) -> Any:
    return json.loads(read_text(path))


def read_yaml(path: str | os.PathLike[str]) -> Any:
    return yaml.safe_load(read_text(path))
