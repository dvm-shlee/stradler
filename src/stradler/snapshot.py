"""Snapshot format version 2: digests, writer and reader (SNAPSHOTS.md).

Section numbers in docstrings refer to ``docs/SNAPSHOTS.md``. The canonical JSON and the digest text form are the
ones of :mod:`stradler.workflow.identity`; no existing digest is changed here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
import time
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from . import verified
from .workflow.areas import AreaIndex
from .workflow.execute import DEFAULT_RECORD_AREA, write_json_atomic
from .workflow.identity import canonical_json, digest_of, sha256_digest

__all__ = [
    "SnapshotError",
    "Tree",
    "build_tree",
    "check_provenance",
    "file_digest",
    "flat_content_digest",
    "iter_entries",
    "node_digest",
    "read_snapshot",
    "snapshot_dir",
    "stream_content_digest",
    "tree_content_digest",
    "write_snapshot",
]

V2_DIR = os.path.join("snapshots", "v2")
_CHUNK = 1 << 20
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_V2_KEYS = {
    "snapshot_version", "algorithm", "layout", "dataset", "digest", "entries", "entries_stream", "root",
    "file_count", "total_size", "created", "previous", "grade", "provenance",
}
_LIMIT = 2**53


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be written or a snapshot document cannot be read."""


# --------------------------------------------------------------------------- digests


def file_digest(path: str | os.PathLike) -> tuple[str, int]:
    """Digest a regular file by streaming it (section 4.4).

    Parameters
    ----------
    path : str or os.PathLike
        File to read.

    Returns
    -------
    tuple of (str, int)
        The digest ``sha256:<hex>`` of the bytes and the number of bytes read.

    Raises
    ------
    SnapshotError
        If the file changed while it was read (size or modification time differ, or the bytes read differ
        from the size).
    """
    h = hashlib.sha256()
    n = 0
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise SnapshotError(f"entry is not a regular file: {os.fspath(path)}")
    active = verified.current()
    if active is not None:
        hit = active.lookup(path)
        if hit is not None:
            return hit
    with open(path, "rb") as fh:
        before = os.fstat(fh.fileno())
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
        after = os.fstat(fh.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or n != after.st_size:
        raise SnapshotError(f"file changed while it was read: {os.fspath(path)}")
    return "sha256:" + h.hexdigest(), n


def flat_content_digest(entries: Iterable[Mapping[str, Any]]) -> str:
    """Return the content digest of a flat snapshot from its sorted entries (section 3.2).

    Parameters
    ----------
    entries : iterable of mapping
        Entries ``{path, digest, size}`` in the order of section 4.2.

    Returns
    -------
    str
        Digest of ``{"algorithm":"sha256","entries":[...],"layout":"flat"}``.
    """
    return digest_of({"algorithm": "sha256", "entries": list(entries), "layout": "flat"})


def stream_content_digest(lines: Iterable[bytes | str]) -> str:
    """Return the flat content digest from an entry stream without holding it (section 5.3).

    Parameters
    ----------
    lines : iterable of bytes or str
        The canonical JSON of each entry, in order, without the line feed.

    Returns
    -------
    str
        The same digest as :func:`flat_content_digest` of the same entries.
    """
    h = hashlib.sha256()
    h.update(b'{"algorithm":"sha256","entries":[')
    first = True
    for line in lines:
        if not first:
            h.update(b",")
        first = False
        h.update(line if isinstance(line, bytes) else line.encode("utf-8"))
    h.update(b'],"layout":"flat"}')
    return "sha256:" + h.hexdigest()


def node_digest(children: Iterable[Mapping[str, Any]]) -> str:
    """Return the digest of a tree node from its sorted child records (section 5.2).

    Parameters
    ----------
    children : iterable of mapping
        Child records ``{name, kind, digest, size}`` sorted by the bytes of ``name``.

    Returns
    -------
    str
        Digest of the canonical JSON of the child array.
    """
    return digest_of(list(children))


def tree_content_digest(root: str) -> str:
    """Return the content digest of a tree snapshot from its root node digest (section 3.2).

    Parameters
    ----------
    root : str
        Digest of the root node.

    Returns
    -------
    str
        Digest of ``{"algorithm":"sha256","layout":"tree","root":root}``.
    """
    return digest_of({"algorithm": "sha256", "layout": "tree", "root": root})


# --------------------------------------------------------------------------- scanning


def _bytes_key(s: str) -> bytes:
    return s.encode("utf-8")


def _check_name(name: str, where: str) -> str:
    try:
        text = os.fsencode(name).decode("utf-8")
    except UnicodeDecodeError:
        raise SnapshotError(f"name is not valid UTF-8: {where!r}") from None
    if text in ("", ".", ".."):
        raise SnapshotError(f"invalid name: {where!r}")
    return text


def _scan(full: str, rel: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(relative path, absolute path)`` of every regular file under ``full`` (unsorted)."""
    with os.scandir(full) as it:
        items = list(it)
    for e in items:
        name = _check_name(e.name, os.path.join(rel, e.name))
        path = f"{rel}/{name}" if rel else name
        st = e.stat(follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            raise SnapshotError(f"symbolic link refused: {path}")
        if stat.S_ISREG(st.st_mode):
            yield path, e.path
        elif stat.S_ISDIR(st.st_mode):
            yield from _scan(e.path, path)
        else:
            raise SnapshotError(f"entry is not a regular file or directory: {path}")


def iter_entries(dataset_dir: str | os.PathLike) -> Iterator[dict[str, Any]]:
    """Yield the entries of a dataset folder in sorted order, one file read at a time (section 4).

    Parameters
    ----------
    dataset_dir : str or os.PathLike
        The dataset folder.

    Yields
    ------
    dict
        ``{path, digest, size}`` for each regular file, sorted by the UTF-8 bytes of the path.

    Raises
    ------
    SnapshotError
        For a symbolic link, a non-regular entry, a name that is not UTF-8, or a file that changes while read.
    """
    full = os.fspath(dataset_dir)
    found = sorted(_scan(full), key=lambda t: _bytes_key(t[0]))
    for path, abspath in found:
        digest, size = file_digest(abspath)
        yield {"path": path, "digest": digest, "size": size}


class Tree:
    """The nodes of a tree layout snapshot (section 5.2).

    Attributes
    ----------
    root : str
        Root node digest.
    nodes : dict
        Node digest to the list of child records of that node.
    dirs : dict
        Directory path (``""`` for the dataset folder) to its node digest.
    file_count : int
        Number of regular files.
    total_size : int
        Sum of their sizes.
    """

    def __init__(self) -> None:
        self.root = ""
        self.nodes: dict[str, list[dict[str, Any]]] = {}
        self.dirs: dict[str, str] = {}
        self.file_count = 0
        self.total_size = 0

    def node_digest(self, path: str = "") -> str:
        """Return the node digest of the directory ``path`` (``""`` is the root).

        Parameters
        ----------
        path : str
            Directory path relative to the dataset folder.

        Returns
        -------
        str
            Its node digest.

        Raises
        ------
        KeyError
            If the path is not a directory node.
        """
        return self.dirs[path]

    @property
    def content_digest(self) -> str:
        """The content digest of the snapshot."""
        return tree_content_digest(self.root)


def build_tree(dataset_dir: str | os.PathLike) -> Tree:
    """Compute the tree nodes of a dataset folder (section 5.2).

    Parameters
    ----------
    dataset_dir : str or os.PathLike
        The dataset folder.

    Returns
    -------
    Tree
        The nodes, their digests and the totals.

    Raises
    ------
    SnapshotError
        As :func:`iter_entries`, and when the folder holds no regular file.
    """
    tree = Tree()
    full = os.fspath(dataset_dir)
    node = _tree_node(full, "", tree)
    if node is None:
        raise SnapshotError("dataset has no regular files")
    tree.root = node[0]
    return tree


def _tree_node(full: str, rel: str, tree: Tree) -> tuple[str, int] | None:
    with os.scandir(full) as it:
        items = list(it)
    children: list[dict[str, Any]] = []
    for e in sorted(items, key=lambda e: os.fsencode(e.name)):
        name = _check_name(e.name, os.path.join(rel, e.name))
        path = f"{rel}/{name}" if rel else name
        st = e.stat(follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            raise SnapshotError(f"symbolic link refused: {path}")
        if stat.S_ISREG(st.st_mode):
            d, n = file_digest(e.path)
            children.append({"name": name, "kind": "file", "digest": d, "size": n})
            tree.file_count += 1
            tree.total_size += n
        elif stat.S_ISDIR(st.st_mode):
            sub = _tree_node(e.path, path, tree)
            if sub is not None:
                children.append({"name": name, "kind": "dir", "digest": sub[0], "size": sub[1]})
        else:
            raise SnapshotError(f"entry is not a regular file or directory: {path}")
    if not children:
        return None
    children.sort(key=lambda c: _bytes_key(c["name"]))
    d = node_digest(children)
    tree.nodes[d] = children
    tree.dirs[rel] = d
    return d, sum(c["size"] for c in children)


# --------------------------------------------------------------------------- storage


def snapshot_dir(root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None = None) -> str:
    """Return the folder of version 2 documents, ``<record area>/snapshots/v2`` (section 6.1).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.

    Returns
    -------
    str
        Absolute folder path.
    """
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    return os.path.join(os.fspath(root), areas.record_path() or DEFAULT_RECORD_AREA, V2_DIR)


def _hex(digest: str) -> str:
    return digest.split(":", 1)[1]


def _write_bytes_atomic(path: str, data: bytes) -> None:
    """Write ``path`` through a temporary name unless it exists (section 6.2)."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _heads(folder: str, dataset: str) -> list[str]:
    docs: dict[str, str | None] = {}
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if f.endswith(".json") and os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                if d.get("dataset") == dataset:
                    docs[d["digest"]] = d.get("previous")
    named = {p for p in docs.values() if p}
    return sorted(d for d in docs if d not in named)


def _makedirs_tracked(path: str) -> list[str]:
    """Create ``path`` and return the directories this call created, outermost first."""
    missing = []
    d = path
    while d and not os.path.isdir(d):
        missing.append(d)
        d = os.path.dirname(d)
    os.makedirs(path, exist_ok=True)
    return missing[::-1]


def check_provenance(provenance: Mapping[str, Any]) -> None:
    """Check the shape of a provenance object (section 3.5); its content is never interpreted.

    ``origin`` is a string, ``checked_at`` a UTC timestamp, ``metadata`` a canonical JSON object. Other
    members are stored as given.
    """
    if not isinstance(provenance, Mapping):
        raise SnapshotError("provenance is not an object")
    if "origin" in provenance and not isinstance(provenance["origin"], str):
        raise SnapshotError("provenance.origin is not a string")
    if "checked_at" in provenance and not (isinstance(provenance["checked_at"], str) and _TIME.match(provenance["checked_at"])):
        raise SnapshotError(f"provenance.checked_at is not a timestamp: {provenance['checked_at']!r}")
    if "metadata" in provenance and not isinstance(provenance["metadata"], Mapping):
        raise SnapshotError("provenance.metadata is not an object")


def write_snapshot(
    root: str | os.PathLike,
    dataset: str,
    structure: Mapping[str, Any] | AreaIndex | None = None,
    *,
    layout: str = "flat",
    stream: bool = False,
    previous: str | None = None,
    grade: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    created: str | None = None,
    reuse: verified.Reuse | None = None,
) -> dict[str, Any]:
    """Write a version 2 snapshot of a dataset folder (sections 3 to 6).

    The dataset folder is not modified. Nodes and the entry stream are written before the document, each
    through a temporary name; a file that already exists is kept.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    dataset : str
        Project-relative dataset folder.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``; names the record area.
    layout : {"flat", "tree"}
        Layout of the entries.
    stream : bool
        For layout ``flat``, store the entries as a stream instead of inline.
    previous : str or None
        Content digest of the head of the dataset's lineage; ``None`` for a first version.
    grade, provenance
        Stored uninterpreted when given.
    created : str or None
        Timestamp ``YYYY-MM-DDTHH:MM:SSZ``; the current UTC time when ``None``.
    reuse : stradler.verified.Reuse or None
        Digest cache: a file whose size and mtime match a cache line is not read. ``None`` reads every file.

    Returns
    -------
    dict
        The document, as stored (the existing document when the content was already written).

    Raises
    ------
    SnapshotError
        For a missing or empty dataset, a refused entry (section 4.3), a changing file, a ``previous`` that is
        not the head of the dataset, or a dataset that already has two heads.
    """
    with verified.reuse_scope(reuse):
        return _write_snapshot(root, dataset, structure, layout, stream, previous, grade, provenance, created)


def _write_snapshot(root, dataset, structure, layout, stream, previous, grade, provenance, created):  # type: ignore[no-untyped-def]
    if layout not in ("flat", "tree"):
        raise SnapshotError(f"unknown layout: {layout!r}")
    if stream and layout != "flat":
        raise SnapshotError("an entry stream exists for layout flat only")
    if previous is not None and not (isinstance(previous, str) and _DIGEST.match(previous)):
        raise SnapshotError(f"previous is not a digest: {previous!r}")
    if provenance is not None:
        check_provenance(provenance)
        try:
            canonical_json(provenance)
        except ValueError as exc:
            raise SnapshotError(f"provenance is not canonical JSON: {exc}") from None
    dataset = dataset.strip("/")
    full = os.path.join(os.fspath(root), dataset)
    if not os.path.isdir(full):
        raise SnapshotError(f"dataset folder does not exist: {dataset}")
    folder = snapshot_dir(root, structure)
    heads = _heads(folder, dataset)
    if len(heads) > 1:
        raise SnapshotError(f"dataset {dataset!r} has two heads: {', '.join(heads)}")

    doc: dict[str, Any] = {
        "snapshot_version": 2, "algorithm": "sha256", "layout": layout, "dataset": dataset,
    }
    pending: list[tuple[str, bytes]] = []
    stream_tmp: str | None = None
    created_dirs: list[str] = []
    ok = False
    try:
        if layout == "tree":
            tree = build_tree(full)
            digest = tree.content_digest
            doc["root"] = tree.root
            count, size = tree.file_count, tree.total_size
            for d, children in tree.nodes.items():
                pending.append((os.path.join(folder, "nodes", _hex(d) + ".json"), canonical_json(children).encode("utf-8")))
        elif stream:
            created_dirs = _makedirs_tracked(folder)
            stream_tmp = os.path.join(folder, f".entries.tmp-{os.getpid()}")
            h = hashlib.sha256()
            h.update(b'{"algorithm":"sha256","entries":[')
            count = size = 0
            with open(stream_tmp, "wb") as fh:
                for e in iter_entries(full):
                    line = canonical_json(e).encode("utf-8")
                    h.update((b"," if count else b"") + line)
                    fh.write(line + b"\n")
                    count += 1
                    size += e["size"]
            h.update(b'],"layout":"flat"}')
            digest = "sha256:" + h.hexdigest()
            if count == 0:
                raise SnapshotError("dataset has no regular files")
            doc["entries_stream"] = _hex(digest) + ".entries.jsonl"
        else:
            entries = list(iter_entries(full))
            if not entries:
                raise SnapshotError("dataset has no regular files")
            digest = flat_content_digest(entries)
            doc["entries"] = entries
            count, size = len(entries), sum(e["size"] for e in entries)
        if count == 0:
            raise SnapshotError("dataset has no regular files")
        if size > _LIMIT:
            raise SnapshotError("total size out of range")
        docpath = os.path.join(folder, _hex(digest) + ".json")
        if os.path.exists(docpath):
            if stream_tmp and os.path.exists(stream_tmp):
                os.unlink(stream_tmp)
            ok = True
            return read_snapshot(docpath)
        if previous != (heads[0] if heads else None):
            raise SnapshotError(
                f"previous {previous!r} is not the head of dataset {dataset!r} ({heads[0] if heads else None!r})"
            )
        doc.update({
            "digest": digest, "file_count": count, "total_size": size,
            "created": created or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "previous": previous,
        })
        if grade is not None:
            doc["grade"] = grade
        if provenance is not None:
            doc["provenance"] = dict(provenance)
        for p, data in pending:
            _write_bytes_atomic(p, data)
        if stream_tmp:
            final = os.path.join(folder, doc["entries_stream"])
            if os.path.exists(final):
                os.unlink(stream_tmp)
            else:
                os.replace(stream_tmp, final)
            stream_tmp = None
        write_json_atomic(docpath, doc)
        ok = True
        return doc
    finally:
        if stream_tmp and os.path.exists(stream_tmp):
            os.unlink(stream_tmp)
        if not ok:
            for d in reversed(created_dirs):
                try:
                    os.rmdir(d)
                except OSError:
                    break


# --------------------------------------------------------------------------- reading


def _check_digest(value: Any, what: str) -> str:
    if not (isinstance(value, str) and _DIGEST.match(value)):
        raise SnapshotError(f"{what} is not a digest: {value!r}")
    return value


def _check_int(value: Any, what: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or value > _LIMIT:
        raise SnapshotError(f"{what} is not an integer in range: {value!r}")
    return value


def _check_segment(name: Any, what: str) -> str:
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:
        raise SnapshotError(f"{what} is not a valid name: {name!r}")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise SnapshotError(f"{what} is not valid UTF-8: {name!r}") from None
    return name


def _check_entry(e: Any) -> dict[str, Any]:
    if not isinstance(e, dict) or set(e) != {"path", "digest", "size"}:
        raise SnapshotError(f"entry has wrong members: {e!r}")
    if not isinstance(e["path"], str) or not e["path"]:
        raise SnapshotError(f"entry path is invalid: {e['path']!r}")
    for seg in e["path"].split("/"):
        _check_segment(seg, "path segment")
    _check_digest(e["digest"], "entry digest")
    _check_int(e["size"], "entry size", 0)
    return e


def _check_sorted(keys: list[bytes], what: str) -> None:
    for a, b in itertools.pairwise(keys):
        if a >= b:
            raise SnapshotError(f"{what} are not strictly sorted by UTF-8 bytes")


def read_snapshot(path: str | os.PathLike) -> dict[str, Any]:
    """Load a version 1 or version 2 snapshot document and check it (section 8).

    Parameters
    ----------
    path : str or os.PathLike
        The document file. For version 2 its stream and nodes are read from the same folder.

    Returns
    -------
    dict
        The document.

    Raises
    ------
    SnapshotError
        For an unknown version, an unknown version 2 key, a malformed field, a digest that is not reproduced,
        or a ``file_count`` or ``total_size`` that differs from the entries.
    """
    path = os.fspath(path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read snapshot document {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise SnapshotError("snapshot document is not an object")
    version = doc.get("snapshot_version", 1)
    if isinstance(version, bool) or version not in (1, 2):
        raise SnapshotError(f"unknown snapshot version: {version!r}")
    if version == 1:
        return _read_v1(doc)
    return _read_v2(doc, os.path.dirname(path))


def _read_v1(doc: dict[str, Any]) -> dict[str, Any]:
    files = doc.get("files")
    if not isinstance(files, list) or not isinstance(doc.get("dataset"), str):
        raise SnapshotError("version 1 document needs dataset and files")
    for e in files:
        _check_entry(e)
    if digest_of(files) != _check_digest(doc.get("digest"), "digest"):
        raise SnapshotError("digest does not match the files")
    return doc


def _read_v2(doc: dict[str, Any], folder: str) -> dict[str, Any]:
    extra = set(doc) - _V2_KEYS
    if extra:
        raise SnapshotError(f"unknown keys in version 2 document: {sorted(extra)}")
    if doc.get("algorithm") != "sha256":
        raise SnapshotError(f"unknown algorithm: {doc.get('algorithm')!r}")
    layout = doc.get("layout")
    if layout not in ("flat", "tree"):
        raise SnapshotError(f"unknown layout: {layout!r}")
    for key in ("dataset", "digest", "file_count", "total_size", "created", "previous"):
        if key not in doc:
            raise SnapshotError(f"missing key: {key}")
    if not isinstance(doc["dataset"], str):
        raise SnapshotError("dataset is not a string")
    digest = _check_digest(doc["digest"], "digest")
    _check_int(doc["file_count"], "file_count", 1)
    _check_int(doc["total_size"], "total_size", 0)
    if not isinstance(doc["created"], str) or not _TIME.match(doc["created"]):
        raise SnapshotError(f"created is not a timestamp: {doc['created']!r}")
    if doc["previous"] is not None:
        _check_digest(doc["previous"], "previous")
    if "grade" in doc and not isinstance(doc["grade"], str):
        raise SnapshotError("grade is not a string")
    if "provenance" in doc:
        if not isinstance(doc["provenance"], dict):
            raise SnapshotError("provenance is not an object")
        check_provenance(doc["provenance"])
        try:
            canonical_json(doc["provenance"])
        except ValueError as exc:
            raise SnapshotError(f"provenance is not canonical JSON: {exc}") from None
    if layout == "flat":
        count, size, computed = _read_flat(doc, folder)
    else:
        count, size, computed = _read_tree(doc, folder)
    if computed != digest:
        raise SnapshotError(f"content digest is not reproduced: {computed} != {digest}")
    if count != doc["file_count"]:
        raise SnapshotError(f"file_count {doc['file_count']} differs from {count}")
    if size != doc["total_size"]:
        raise SnapshotError(f"total_size {doc['total_size']} differs from {size}")
    return doc


def _read_flat(doc: dict[str, Any], folder: str) -> tuple[int, int, str]:
    if "root" in doc or ("entries" in doc) == ("entries_stream" in doc):
        raise SnapshotError("flat layout needs exactly one of entries and entries_stream, and no root")
    if "entries" in doc:
        entries = doc["entries"]
        if not isinstance(entries, list) or not entries:
            raise SnapshotError("entries is not a non-empty array")
        for e in entries:
            _check_entry(e)
        _check_sorted([_bytes_key(e["path"]) for e in entries], "entries")
        return len(entries), sum(e["size"] for e in entries), flat_content_digest(entries)
    name = doc["entries_stream"]
    if not isinstance(name, str) or "/" in name or name in ("", ".", ".."):
        raise SnapshotError(f"entries_stream is not a file name: {name!r}")
    count = size = 0
    prev: bytes | None = None

    def gen() -> Iterator[bytes]:
        nonlocal count, size, prev
        with open(os.path.join(folder, name), "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    raise SnapshotError("entry stream line without line feed")
                line = raw[:-1]
                e = _check_entry(json.loads(line.decode("utf-8")))
                if canonical_json(e).encode("utf-8") != line:
                    raise SnapshotError("entry stream line is not canonical JSON")
                key = _bytes_key(e["path"])
                if prev is not None and prev >= key:
                    raise SnapshotError("entries are not strictly sorted by UTF-8 bytes")
                prev = key
                count += 1
                size += e["size"]
                yield line

    try:
        computed = stream_content_digest(gen())
    except (OSError, ValueError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f"cannot read entry stream {name}: {exc}") from None
    if count == 0:
        raise SnapshotError("entry stream is empty")
    return count, size, computed


def _read_tree(doc: dict[str, Any], folder: str) -> tuple[int, int, str]:
    if "entries" in doc or "entries_stream" in doc or "root" not in doc:
        raise SnapshotError("tree layout needs root and no entries")
    root = _check_digest(doc["root"], "root")
    count = 0

    def node(d: str) -> int:
        nonlocal count
        p = os.path.join(folder, "nodes", _hex(d) + ".json")
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise SnapshotError(f"cannot read node {d}: {exc}") from None
        if sha256_digest(data) != d:
            raise SnapshotError(f"node file does not match its digest: {d}")
        children = json.loads(data.decode("utf-8"))
        if not isinstance(children, list) or not children:
            raise SnapshotError(f"node {d} is not a non-empty array")
        if canonical_json(children).encode("utf-8") != data:
            raise SnapshotError(f"node {d} is not canonical JSON")
        total = 0
        for c in children:
            if not isinstance(c, dict) or set(c) != {"name", "kind", "digest", "size"} or c["kind"] not in ("file", "dir"):
                raise SnapshotError(f"node {d} has a malformed child record")
            _check_segment(c["name"], "child name")
            _check_digest(c["digest"], "child digest")
            _check_int(c["size"], "child size", 0)
            if c["kind"] == "dir":
                if node(c["digest"]) != c["size"]:
                    raise SnapshotError(f"size of directory {c['name']!r} differs from its node")
            else:
                count += 1
            total += c["size"]
        _check_sorted([_bytes_key(c["name"]) for c in children], "children")
        return total

    size = node(root)
    return count, size, tree_content_digest(root)
