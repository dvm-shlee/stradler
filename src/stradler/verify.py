"""Check a snapshot against a dataset folder or a store copy (SNAPSHOTS.md section 7).

Nothing here writes inside the dataset or the store. The only write is the summary check record under the record
area (section 7.4).
"""

from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Mapping
from typing import Any

from .snapshot import (
    SnapshotError,
    _hex,
    file_digest,
    read_snapshot,
    snapshot_dir,
)
from .workflow.areas import AreaIndex
from .workflow.execute import DEFAULT_RECORD_AREA

__all__ = [
    "EXIT_DIFFERS",
    "EXIT_ERROR",
    "EXIT_MATCH",
    "EXIT_UNREACHABLE",
    "VerifyError",
    "compare",
    "exit_code",
    "find_document",
    "load_entries",
    "verify_dataset",
    "write_check_record",
]

EXIT_MATCH, EXIT_DIFFERS, EXIT_UNREACHABLE, EXIT_ERROR = 0, 1, 2, 3
CATEGORIES = ("match", "missing", "added", "changed", "unreadable")


class VerifyError(Exception):
    """Raised when a check cannot be made (exit status 3)."""


def exit_code(result: str) -> int:
    """Return the exit status of a result value (0 match, 1 differs, 2 unreachable)."""
    return {"match": EXIT_MATCH, "differs": EXIT_DIFFERS, "unreachable": EXIT_UNREACHABLE}[result]


def _docs(folder: str) -> list[tuple[str, dict]]:
    out = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if f.endswith(".json") and os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict):
                    out.append((p, d))
    return out


def find_document(root: str, structure: Mapping[str, Any] | AreaIndex | None, target: str) -> str:
    """Find the document of a dataset's head or of a digest.

    Parameters
    ----------
    root : str
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    target : str
        A ``sha256:`` digest (version 2 first, then version 1), or a project-relative dataset folder (its
        version 2 head).

    Returns
    -------
    str
        Path of the document file.

    Raises
    ------
    VerifyError
        If no document or more than one head is found.
    """
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    v2 = snapshot_dir(root, areas)
    if target.startswith("sha256:") and len(target) == 71:
        for folder in (v2, os.path.dirname(v2)):
            p = os.path.join(folder, _hex(target) + ".json")
            if os.path.isfile(p):
                return p
        raise VerifyError(f"no snapshot document for {target}")
    dataset = target.strip("/")
    docs = {d["digest"]: (p, d) for p, d in _docs(v2) if d.get("dataset") == dataset and "digest" in d}
    named = {d.get("previous") for _, d in docs.values() if d.get("previous")}
    heads = sorted(k for k in docs if k not in named)
    if not heads:
        raise VerifyError(f"no snapshot of dataset {dataset!r}")
    if len(heads) > 1:
        raise VerifyError(f"dataset {dataset!r} has two heads: {', '.join(heads)}")
    return docs[heads[0]][0]


def load_entries(doc: Mapping[str, Any], folder: str) -> dict[str, dict[str, Any]]:
    """Return the entries of a checked document by path, for any version and layout.

    Parameters
    ----------
    doc : mapping
        A document accepted by :func:`stradler.snapshot.read_snapshot`.
    folder : str
        Folder of the document (its stream and nodes lie there).

    Returns
    -------
    dict
        Path to ``{path, digest, size}``.
    """
    if doc.get("snapshot_version", 1) == 1:
        return {e["path"]: e for e in doc["files"]}
    if "entries" in doc:
        return {e["path"]: e for e in doc["entries"]}
    if "entries_stream" in doc:
        out = {}
        with open(os.path.join(folder, doc["entries_stream"]), "rb") as fh:
            for raw in fh:
                e = json.loads(raw)
                out[e["path"]] = e
        return out
    out = {}

    def walk(d: str, prefix: str) -> None:
        with open(os.path.join(folder, "nodes", _hex(d) + ".json"), encoding="utf-8") as fh:
            for c in json.load(fh):
                path = f"{prefix}/{c['name']}" if prefix else c["name"]
                if c["kind"] == "dir":
                    walk(c["digest"], path)
                else:
                    out[path] = {"path": path, "digest": c["digest"], "size": c["size"]}

    walk(doc["root"], "")
    return out


def _subtree_exists(doc: Mapping[str, Any], folder: str, sub: str) -> bool:
    d = doc["root"]
    for seg in sub.split("/"):
        with open(os.path.join(folder, "nodes", _hex(d) + ".json"), encoding="utf-8") as fh:
            kids = {c["name"]: c for c in json.load(fh)}
        c = kids.get(seg)
        if c is None or c["kind"] != "dir":
            return False
        d = c["digest"]
    return True


class _Unreachable(Exception):
    pass


def compare(
    entries: Mapping[str, Mapping[str, Any]],
    store_dir: str,
    *,
    follow_links: bool = False,
    subtree: str | None = None,
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Compare snapshot entries with the regular files below ``store_dir`` (section 7.2).

    Parameters
    ----------
    entries : mapping
        Path to entry.
    store_dir : str
        The dataset folder to read.
    follow_links : bool
        Follow symbolic links to regular files (version 1 snapshots).
    subtree : str or None
        Restrict both sides to this directory.

    Returns
    -------
    tuple
        Counts by category and the per-path lists (names of paths, not stored anywhere).

    Raises
    ------
    _Unreachable
        When the folder cannot be listed.
    """
    wanted = {p: e for p, e in entries.items() if subtree is None or p.startswith(subtree + "/")}
    lists: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    seen: set[str] = set()

    def onerror(exc: OSError) -> None:
        raise _Unreachable(str(exc))

    base = os.path.join(store_dir, subtree) if subtree else store_dir
    prefix = f"{subtree}/" if subtree else ""
    if not os.path.isdir(base):
        base = None  # a reachable store without the folder: every entry is missing
    for cur, dirs, files in (os.walk(base, onerror=onerror) if base else []):
        rel = os.path.relpath(cur, base)
        rel = prefix.rstrip("/") if rel == "." else f"{prefix}{rel}".replace(os.sep, "/")
        for name in sorted(files + [d for d in dirs if os.path.islink(os.path.join(cur, d))]):
            full = os.path.join(cur, name)
            try:
                text = os.fsencode(name).decode("utf-8")
                bad = False
            except UnicodeDecodeError:
                text, bad = os.fsdecode(name), True
            path = f"{rel}/{text}" if rel else text
            seen.add(path)
            st = os.lstat(full)
            islink = stat.S_ISLNK(st.st_mode)
            regular = stat.S_ISREG(st.st_mode)
            if islink and follow_links:
                try:
                    regular = stat.S_ISREG(os.stat(full).st_mode)
                except OSError:
                    regular = False
            elif islink and name in dirs:
                regular = False
            e = wanted.get(path)
            if bad:
                lists["unreadable"].append(path)
            elif e is None:
                if islink and follow_links and not regular:
                    continue
                lists["added"].append(path)
            elif not regular:
                lists["missing"].append(path)
            else:
                try:
                    digest, size = file_digest(full) if not islink else _digest_followed(full)
                except (OSError, SnapshotError):
                    lists["unreadable"].append(path)
                    continue
                lists["match" if (digest, size) == (e["digest"], e["size"]) else "changed"].append(path)
    lists["missing"] += sorted(p for p in wanted if p not in seen)
    counts = {c: len(v) for c, v in lists.items()}
    return counts, lists


def _digest_followed(path: str) -> tuple[str, int]:
    import hashlib
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return "sha256:" + h.hexdigest(), n


def write_check_record(
    root: str, structure: Mapping[str, Any] | AreaIndex | None, store: str, doc: Mapping[str, Any],
    result: str, counts: Mapping[str, int] | None,
) -> str:
    """Write the summary check record of section 7.4 and return its path.

    The record holds no path and no content. An existing record is never overwritten.
    """
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    folder = os.path.join(root, areas.record_path() or DEFAULT_RECORD_AREA, "checks")
    os.makedirs(folder, exist_ok=True)
    now = time.gmtime()
    rec: dict[str, Any] = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", now), "store": store, "snapshot": doc["digest"],
        "snapshot_version": doc.get("snapshot_version", 1), "result": result,
    }
    if result != "unreachable":
        rec["counts"] = dict(counts or {})
    stem = f"{time.strftime('%Y%m%dT%H%M%SZ', now)}-{store}-{_hex(doc['digest'])}"
    data = json.dumps(rec, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    n = 0
    while True:
        path = os.path.join(folder, f"{stem}{'' if n == 0 else '-' + str(n)}.json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path


def verify_dataset(
    root: str,
    structure: Mapping[str, Any] | None,
    target: str,
    *,
    store: str | None = None,
    local: Mapping[str, Any] | None = None,
    subtree: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Verify a dataset or a digest, in the project or in a store copy (section 7).

    Parameters
    ----------
    root : str
        Project root.
    structure : mapping or None
        Parsed ``structure.toml``.
    target : str
        Project-relative dataset folder, or a ``sha256:`` digest.
    store : str or None
        Store name; the copy lies at ``[stores.<name>] path`` of ``local.toml``, then the dataset folder.
    local : mapping or None
        Parsed ``local.toml``.
    subtree : str or None
        One directory of a tree-layout snapshot.
    record : bool
        Write the check record.

    Returns
    -------
    dict
        ``result``, ``counts`` (absent when unreachable), ``snapshot``, ``dataset``, ``lists`` and ``record``.

    Raises
    ------
    VerifyError
        When no valid snapshot is found or the request cannot be made (no record is written).
    """
    areas = AreaIndex(structure)
    docpath = find_document(root, areas, target)
    try:
        doc = read_snapshot(docpath)
        entries = load_entries(doc, os.path.dirname(docpath))
    except (SnapshotError, OSError, ValueError) as exc:
        raise VerifyError(f"snapshot document is not valid: {exc}") from None
    sub = subtree.strip("/") if subtree else None
    if sub:
        if doc.get("snapshot_version", 1) != 2 or doc["layout"] != "tree":
            raise VerifyError("--subtree needs a snapshot of layout tree")
        if not _subtree_exists(doc, os.path.dirname(docpath), sub):
            raise VerifyError(f"no directory {sub!r} in the snapshot")
    dataset = doc["dataset"]
    if store is None:
        folder, name = os.path.join(root, dataset), "local"
    else:
        name = store
        spath = (((local or {}).get("stores") or {}).get(store) or {}).get("path")
        if not isinstance(spath, str):
            raise VerifyError(f"local.toml has no [stores.{store}] path")
        folder = os.path.join(spath, dataset)
        if not os.path.isdir(spath):
            return _finish(root, areas, name, doc, dataset, "unreachable", None, {}, record)
    try:
        counts, lists = compare(entries, folder, follow_links=doc.get("snapshot_version", 1) == 1, subtree=sub)
    except _Unreachable:
        return _finish(root, areas, name, doc, dataset, "unreachable", None, {}, record)
    result = "match" if all(counts[c] == 0 for c in CATEGORIES[1:]) else "differs"
    return _finish(root, areas, name, doc, dataset, result, counts, lists, record)


def _finish(root, areas, name, doc, dataset, result, counts, lists, record) -> dict[str, Any]:
    path = write_check_record(root, areas, name, doc, result, counts) if record else None
    out: dict[str, Any] = {
        "result": result, "snapshot": doc["digest"], "snapshot_version": doc.get("snapshot_version", 1),
        "dataset": dataset, "store": name, "record": path,
    }
    if counts is not None:
        out["counts"] = counts
        out["lists"] = {c: sorted(v, key=_bytes_key_safe) for c, v in lists.items() if c != "match" and v}
    return out


def _bytes_key_safe(s: str) -> bytes:
    return os.fsencode(s)
