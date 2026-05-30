"""Atomic YAML write + numbered backup rotation.

`os.replace` is atomic on POSIX (the rename(2) syscall is atomic). On Windows
it is best-effort safe. We dump YAML in block style with sort_keys=False so the
order matches what the wizard composed (which the user expects: vms first,
then enablement, then pipelines).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BACKUPS = 3


def dump_yaml(data: dict[str, Any]) -> str:
    """Dump a dict to YAML in deterministic block style, keys in insertion order."""
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        indent=2,
        width=100,
    )


def write_atomic(path: Path, content: str) -> None:
    """Write `content` to `path` atomically: temp file → os.replace.

    Temp file is created in the same directory so the rename is on the same
    filesystem (rename(2) requires same-FS).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
        tmp = Path(f.name)
    try:
        os.replace(tmp, path)
    except Exception:
        # Clean up the temp file on rename failure.
        tmp.unlink(missing_ok=True)
        raise


def backup_existing(path: Path, *, keep: int = DEFAULT_BACKUPS) -> Path | None:
    """Rotate `path` → `path.bak.1`, existing `bak.N` → `bak.(N+1)`.

    Caps at `keep` backups (e.g. `.bak.1..3`); the oldest beyond that is dropped.
    Returns the new backup path, or None if there was nothing to back up.
    """
    path = Path(path)
    if not path.exists():
        return None

    # Drop the oldest if it would exceed `keep`.
    oldest = path.with_name(path.name + f".bak.{keep}")
    if oldest.exists():
        oldest.unlink()

    # Shift .bak.(N-1) → .bak.N, ..., .bak.1 → .bak.2
    for i in range(keep - 1, 0, -1):
        src = path.with_name(path.name + f".bak.{i}")
        dst = path.with_name(path.name + f".bak.{i + 1}")
        if src.exists():
            src.rename(dst)

    # Move current → .bak.1
    dst1 = path.with_name(path.name + ".bak.1")
    path.rename(dst1)
    return dst1


def write_yaml_with_backup(
    path: Path, data: dict[str, Any], *, keep_backups: int = DEFAULT_BACKUPS
) -> Path | None:
    """High-level helper: rotate backups, then atomically write new YAML.

    Returns the path of the created backup (or None if `path` did not exist).
    """
    backup = backup_existing(path, keep=keep_backups)
    write_atomic(path, dump_yaml(data))
    return backup
