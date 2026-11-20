"""Export of outputs as sealed datasets (design 6)."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from typing import Any

from .areas import AreaIndex
from .execute import DEFAULT_RECORD_AREA, write_json_atomic
from .identity import digest_of, sha256_digest

__all__ = ["ExportError", "export_sealed"]

SNAPSHOT_DIR = "snapshots"
_WRITE = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


class ExportError(ValueError):
    """Raised when a dataset cannot be exported as a sealed snapshot."""


def _files(full: str) -> list[str]:
    out = []
    for dirpath, _d, names in os.walk(full):
        for n in names:
            out.append(os.path.relpath(os.path.join(dirpath, n), full).replace(os.sep, "/"))
    return sorted(out, key=lambda s: s.encode("utf-8"))


def export_sealed(root: str | os.PathLike, dataset: str, structure: Mapping[str, Any] | AreaIndex | None) -> dict[str, Any]:
    """Seal a dataset folder: write its snapshot manifest and make its files read-only.

    The manifest lists every file with its SHA-256 digest; the snapshot digest is the digest of the canonical
    JSON of that list. It is written to ``<record area>/snapshots/<hex>.json``, not inside the dataset. The
    dataset must lie in an area of mutability ``sealed``. A dataset already sealed with other content is
    refused: a change makes a new versioned dataset.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    dataset : str
        Project-relative dataset folder.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.

    Returns
    -------
    dict
        The snapshot: ``dataset``, ``files`` (list of ``{path, digest, size}``) and ``digest``.

    Raises
    ------
    ExportError
        If the dataset is missing, empty, outside a sealed area, or already sealed with different content.
    """
    root = os.fspath(root)
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    dataset = dataset.strip("/")
    full = os.path.join(root, dataset)
    if not os.path.isdir(full):
        raise ExportError(f"dataset folder does not exist: {dataset}")
    if areas.mutability_of(dataset) != "sealed":
        raise ExportError(f"dataset {dataset!r} is not in an area of mutability sealed")
    names = _files(full)
    if not names:
        raise ExportError(f"dataset {dataset!r} has no files")
    entries = []
    for n in names:
        with open(os.path.join(full, n), "rb") as fh:
            data = fh.read()
        entries.append({"path": n, "digest": sha256_digest(data), "size": len(data)})
    digest = digest_of(entries)
    snap = {"dataset": dataset, "files": entries, "digest": digest}
    fname = digest.split(":")[1] + ".json"
    rec = os.path.join(root, areas.record_path() or DEFAULT_RECORD_AREA, SNAPSHOT_DIR)
    if os.path.isdir(rec):
        for f in sorted(os.listdir(rec)):
            if f.endswith(".json") and f != fname:
                with open(os.path.join(rec, f), encoding="utf-8") as fh:
                    other = json.load(fh)
                if other.get("dataset") == dataset:
                    raise ExportError(f"dataset {dataset!r} is already sealed as {other['digest']}; use a new versioned dataset")
    write_json_atomic(os.path.join(rec, fname), snap)
    for dirpath, _dirs, files in os.walk(full, topdown=False):
        for n in files:
            p = os.path.join(dirpath, n)
            os.chmod(p, os.stat(p).st_mode & ~_WRITE)
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~_WRITE)
    return snap
