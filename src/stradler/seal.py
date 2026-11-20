"""Seal a dataset: journal, read-only permissions, version 2 document, verify (SNAPSHOTS.md sections 2.5, 6, 7).

The seal journal is written before any permission changes, so that a killed seal can be completed by running
``seal`` again or undone by ``seal --rollback``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from typing import Any

from .snapshot import (
    SnapshotError,
    _hex,
    build_tree,
    flat_content_digest,
    iter_entries,
    snapshot_dir,
    stream_content_digest,
    write_snapshot,
)
from .verify import VerifyError, find_document, verify_dataset
from .workflow.areas import AreaIndex
from .workflow.execute import write_json_atomic
from .workflow.identity import canonical_json

__all__ = ["SealError", "journal_path", "rollback_seal", "seal_dataset"]


class SealError(Exception):
    """Raised when a seal or a rollback is refused or cannot continue."""


def _checkpoint(step: str) -> None:
    """Hook called between the steps of a seal; tests replace it to simulate an interruption."""


def journal_path(root: str, structure: Mapping[str, Any] | AreaIndex | None, dataset: str) -> str:
    """Return the journal file of a dataset, ``<record>/snapshots/v2/pending/<hash>.json``."""
    name = hashlib.sha256(dataset.encode("utf-8")).hexdigest()
    return os.path.join(snapshot_dir(root, structure), "pending", name + ".json")


def _walk(full: str) -> tuple[list[str], list[str]]:
    """Return relative paths of regular files and of directories (the root is ``""``)."""
    files, dirs = [], [""]
    for cur, dnames, fnames in os.walk(full):
        rel = os.path.relpath(cur, full)
        rel = "" if rel == "." else rel
        for d in dnames:
            if stat.S_ISDIR(os.lstat(os.path.join(cur, d)).st_mode):
                dirs.append(os.path.join(rel, d))
        for f in fnames:
            if stat.S_ISREG(os.lstat(os.path.join(cur, f)).st_mode):
                files.append(os.path.join(rel, f))
    return files, dirs


def _content_digest(full: str, layout: str, stream: bool) -> str:
    if layout == "tree":
        return build_tree(full).content_digest
    if stream:
        return stream_content_digest(canonical_json(e).encode("utf-8") for e in iter_entries(full))
    return flat_content_digest(iter_entries(full))


def _chmod(full: str, rel: str, mode: int) -> None:
    os.chmod(os.path.join(full, rel) if rel else full, mode)


def seal_dataset(
    root: str,
    structure: Mapping[str, Any] | None,
    dataset: str,
    *,
    local: Mapping[str, Any] | None = None,
    layout: str = "flat",
    stream: bool = False,
    grade: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal a dataset of an area of mutability ``sealed`` and verify it.

    The steps are: compute the intended digest; write the journal; make files and directories read-only; write
    the version 2 document (``previous`` is the current head); remove the journal; verify. With a journal
    present the run resumes with the journal's own options when the content digest still equals the journal's,
    and refuses otherwise.

    Returns
    -------
    dict
        The verify result (:func:`stradler.verify.verify_dataset`) with ``sealed`` and ``resumed`` added.

    Raises
    ------
    SealError
        For a dataset outside a ``sealed`` area, a content that differs from the journal, or any snapshot error.
    """
    areas = AreaIndex(structure)
    dataset = dataset.strip("/")
    if layout not in ("flat", "tree") or (stream and layout != "flat"):
        raise SealError("layout must be flat or tree; --stream needs layout flat")
    if not dataset or dataset.startswith("..") or not os.path.isdir(os.path.join(root, dataset)):
        raise SealError(f"dataset folder does not exist in the project: {dataset!r}")
    if areas.mutability_of(dataset) != "sealed":
        raise SealError(f"dataset {dataset!r} does not lie in an area of mutability 'sealed'")
    full = os.path.join(root, dataset)
    jpath = journal_path(root, areas, dataset)
    resumed = os.path.exists(jpath)
    try:
        if resumed:
            with open(jpath, encoding="utf-8") as fh:
                j = json.load(fh)
            layout, stream = j["layout"], j["stream"]
            grade, provenance = j.get("grade"), j.get("provenance")
            if _content_digest(full, layout, stream) != j["digest"]:
                raise SealError(
                    f"content of {dataset!r} differs from the interrupted seal; "
                    f"run: stradler seal --rollback {dataset}"
                )
        else:
            digest = _content_digest(full, layout, stream)
            files, dirs = _walk(full)
            modes = {r: stat.S_IMODE(os.lstat(os.path.join(full, r) if r else full).st_mode) for r in files + dirs}
            j = {"dataset": dataset, "digest": digest, "layout": layout, "stream": stream, "modes": modes}
            if grade is not None:
                j["grade"] = grade
            if provenance is not None:
                j["provenance"] = dict(provenance)
            write_json_atomic(jpath, j)
        _checkpoint("journal")
        files, dirs = _walk(full)
        for rel in files + sorted(dirs, key=lambda r: -r.count(os.sep)):
            _chmod(full, rel, stat.S_IMODE(os.lstat(os.path.join(full, rel) if rel else full).st_mode) & ~0o222)
            _checkpoint("chmod")
        _checkpoint("permissions")
        previous = None
        try:
            head = find_document(root, areas, dataset)
            with open(head, encoding="utf-8") as fh:
                previous = json.load(fh)["digest"]
        except VerifyError:
            pass
        doc = write_snapshot(
            root, dataset, areas, layout=layout, stream=stream, previous=previous, grade=grade,
            provenance=provenance,
        )
        if doc["digest"] != j["digest"]:
            raise SealError(
                f"content of {dataset!r} changed during the seal; run: stradler seal --rollback {dataset}"
            )
        _checkpoint("document")
        os.unlink(jpath)
        _checkpoint("cleanup")
    except SnapshotError as exc:
        raise SealError(str(exc)) from None
    try:
        res = verify_dataset(root, structure, dataset, local=local)
    except VerifyError as exc:
        raise SealError(f"sealed, but verify failed: {exc}") from None
    res["sealed"] = True
    res["resumed"] = resumed
    return res


def rollback_seal(root: str, structure: Mapping[str, Any] | None, dataset: str) -> dict[str, Any]:
    """Undo an interrupted seal: restore the recorded modes, remove the journal and any partial document.

    Raises
    ------
    SealError
        When no journal exists for the dataset.
    """
    areas = AreaIndex(structure)
    dataset = dataset.strip("/")
    jpath = journal_path(root, areas, dataset)
    if not os.path.exists(jpath):
        raise SealError(f"no seal journal for {dataset!r}")
    with open(jpath, encoding="utf-8") as fh:
        j = json.load(fh)
    full = os.path.join(root, dataset)
    modes: dict[str, int] = j["modes"]
    # files first, then directories deepest first: a directory is restored after what it holds
    for rel in sorted(modes, key=lambda r: (r == "" or os.path.isdir(os.path.join(full, r)), -r.count(os.sep))):
        p = os.path.join(full, rel) if rel else full
        if os.path.lexists(p):
            os.chmod(p, modes[rel])
    folder = snapshot_dir(root, areas)
    hexd = _hex(j["digest"])
    removed = []
    for name in (hexd + ".json", hexd + ".entries.jsonl"):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            os.unlink(p)
            removed.append(name)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if ".tmp-" in name and os.path.isfile(os.path.join(folder, name)):
                os.unlink(os.path.join(folder, name))
    os.unlink(jpath)
    return {"dataset": dataset, "restored": len(modes), "removed_documents": len(removed)}
