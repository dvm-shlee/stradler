"""Machine-local cache of digests that ``adopt`` has verified (``.stradler/local/verified.jsonl``).

One JSON object per line: ``{verified_version, dest, size, mtime_ns, sha256, verified_at}``. ``dest`` is the
project-relative POSIX path. The cache is not a record: it is git-ignored, may be deleted at any time, and
``snapshot``/``seal`` read it only on request (``--reuse-verified``). A malformed line is ignored and counted.
Everything about the cache lives in this module.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

__all__ = ["CACHE_RELPATH", "Reuse", "current", "ensure_ignored", "load", "record", "reuse_scope"]

CACHE_RELPATH = os.path.join(".stradler", "local", "verified.jsonl")
IGNORE_LINE = "local/"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def ensure_ignored(root: str | os.PathLike) -> None:
    """Make sure ``.stradler/.gitignore`` lists ``local/``."""
    path = Path(root) / ".stradler" / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if IGNORE_LINE not in text.splitlines():
        path.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if not text or text.endswith("\n") else "\n"
        path.write_text(text + sep + IGNORE_LINE + "\n", encoding="utf-8")


def record(root: str | os.PathLike, dest: str, size: int, mtime_ns: int, digest: str) -> None:
    """Append one verified line for ``dest`` (project-relative) to the cache."""
    path = Path(root) / CACHE_RELPATH
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created:
        ensure_ignored(root)
    line = {
        "verified_version": 1, "dest": dest.replace(os.sep, "/"), "size": size, "mtime_ns": mtime_ns,
        "sha256": digest, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")


def load(root: str | os.PathLike) -> tuple[dict[str, tuple[int, int, str]], int]:
    """Read the cache. Returns ``({dest: (size, mtime_ns, digest)}, malformed_line_count)``; latest line wins."""
    path = Path(root) / CACHE_RELPATH
    out: dict[str, tuple[int, int, str]] = {}
    bad = 0
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return out, 0
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
            ok = bool(
                isinstance(d, dict) and d.get("verified_version") == 1 and isinstance(d.get("dest"), str)
                and type(d.get("size")) is int and type(d.get("mtime_ns")) is int
                and isinstance(d.get("sha256"), str) and _DIGEST.match(d["sha256"])
            )
        except ValueError:
            ok = False
        if not ok:
            bad += 1
            continue
        out[d["dest"]] = (d["size"], d["mtime_ns"], d["sha256"])
    return out, bad


class Reuse:
    """Digest lookup over the cache for one run; counts reused files and malformed lines."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = os.path.realpath(root)
        self.entries, self.warnings = load(root)
        self.reused: set[str] = set()

    def lookup(self, path: str | os.PathLike) -> tuple[str, int] | None:
        """Return ``(digest, size)`` when size and mtime_ns of ``path`` match its cache line, else ``None``."""
        try:
            p = os.fspath(path)
            full = os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))
            rel = os.path.relpath(full, self.root).replace(os.sep, "/")
            hit = self.entries.get(rel)
            if hit is None:
                return None
            st = os.lstat(p)
        except (OSError, ValueError):
            return None
        if (st.st_size, st.st_mtime_ns) != (hit[0], hit[1]):
            return None
        self.reused.add(rel)
        return hit[2], hit[0]

    @property
    def count(self) -> int:
        """Number of distinct files whose digest was taken from the cache."""
        return len(self.reused)


_current: list[Reuse | None] = [None]


def current() -> Reuse | None:
    """The active :class:`Reuse`, if a scope is open."""
    return _current[0]


@contextlib.contextmanager
def reuse_scope(reuse: Reuse | None) -> Iterator[None]:
    """Make ``reuse`` the digest source of :func:`stradler.snapshot.file_digest` inside the block."""
    prev = _current[0]
    _current[0] = reuse if reuse is not None else prev
    try:
        yield
    finally:
        _current[0] = prev
