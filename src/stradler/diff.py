"""Version diff: snapshot against snapshot or against the working copy (docs/DIFF.md).

Between two snapshots only documents (and their entry streams or tree nodes) are read; no content file is touched,
so a diff works on an expected or partial dataset. ``--to working`` lists the regular files of the dataset folder
and hashes them (depth ``sha256``) or compares sizes only (depth ``size``). Nothing is written.

``relation`` says how the ``to`` side stands to the ``from`` side: ``descendant`` (``to`` follows ``from`` in the
lineage, also the first version against empty), ``ancestor``, ``same`` or ``unrelated``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import stat
from collections.abc import Mapping, Sequence
from typing import Any

from .snapshot import SnapshotError, file_digest, read_snapshot, snapshot_dir
from .verify import VerifyError, find_document, load_entries
from .workflow.areas import AreaIndex

__all__ = ["DiffError", "classify", "diff_dataset", "exit_code", "render_text"]

EXIT_SAME, EXIT_DIFFERS, EXIT_ERROR = 0, 1, 3


class DiffError(Exception):
    """Raised when a diff cannot be made (exit status 3)."""


def exit_code(result: Mapping[str, Any]) -> int:
    """Return 0 when the two sides do not differ, 1 when they do."""
    c = result["counts"]
    return EXIT_DIFFERS if c["added"] or c["removed"] or c["changed"] or c["moved"] or c["unreadable"] else EXIT_SAME


def _key(s: str) -> bytes:
    return s.encode("utf-8")


def _select(path: str, paths: Sequence[str], include: Sequence[str], exclude: Sequence[str]) -> bool:
    if paths and not any(path == p.strip("/") or path.startswith(p.strip("/") + "/") for p in paths):
        return False
    if include and not any(fnmatch.fnmatchcase(path, g) for g in include):
        return False
    return not any(fnmatch.fnmatchcase(path, g) for g in exclude)


def classify(old: Mapping[str, Mapping[str, Any]], new: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Classify two entry maps (path to ``{digest, size}``) into added, removed, changed, moved and unchanged."""
    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unchanged = 0
    for p in sorted(old.keys() | new.keys(), key=_key):
        o, n = old.get(p), new.get(p)
        if o is not None and n is not None:
            if o["digest"] == n["digest"]:
                unchanged += 1
            else:
                changed.append({"path": p, "from": {"size": o["size"], "digest": o["digest"]},
                                "to": {"size": n["size"], "digest": n["digest"]}})
        elif o is not None:
            removed.append({"path": p, "size": o["size"], "digest": o["digest"]})
        elif n is not None:
            added.append({"path": p, "size": n["size"], "digest": n["digest"]})
    # Move pass: one to one per digest in sorted path order; empty files are never paired.
    rem_by: dict[str, list[dict]] = {}
    for r in removed:
        if r["size"] > 0:
            rem_by.setdefault(r["digest"], []).append(r)
    moved: list[dict] = []
    paired_rem: set[str] = set()
    paired_add: set[str] = set()
    for a in added:
        lst = rem_by.get(a["digest"])
        if a["size"] > 0 and lst:
            r = lst.pop(0)
            moved.append({"from_path": r["path"], "to_path": a["path"], "size": a["size"], "digest": a["digest"]})
            paired_rem.add(r["path"])
            paired_add.add(a["path"])
    by_digest: dict[str, str] = {}
    for p in sorted(old, key=_key):
        by_digest.setdefault(old[p]["digest"], p)
    out_added = []
    for a in added:
        if a["path"] in paired_add:
            continue
        same = by_digest.get(a["digest"]) if a["size"] > 0 else None
        out_added.append({**a, "same_content_as": same})
    return {
        "added": out_added,
        "removed": [r for r in removed if r["path"] not in paired_rem],
        "changed": changed,
        "moved": moved,
        "unchanged": unchanged,
    }


def _scan_working(folder: str, depth: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """List the regular files of a dataset folder (SNAPSHOTS 4.3) as entries, and the unreadable ones with a reason."""
    ents: dict[str, dict[str, Any]] = {}
    bad: dict[str, str] = {}

    def walk(full: str, rel: str) -> None:
        try:
            with os.scandir(full) as it:
                items = list(it)
        except OSError as e:
            bad[rel] = (e.strerror or "cannot read").lower()
            return
        for e in items:
            path = f"{rel}/{e.name}" if rel else e.name
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                bad[path] = (ex.strerror or "cannot read").lower()
                continue
            if stat.S_ISLNK(st.st_mode):
                bad[path] = "symbolic link"
            elif stat.S_ISDIR(st.st_mode):
                walk(e.path, path)
            elif not stat.S_ISREG(st.st_mode):
                bad[path] = "not a regular file"
            elif depth == "size":
                ents[path] = {"size": st.st_size, "digest": None}
            else:
                try:
                    d, n = file_digest(e.path)
                except OSError as ex:
                    bad[path] = (ex.strerror or "cannot read").lower()
                except SnapshotError:
                    bad[path] = "changed while read"
                else:
                    ents[path] = {"size": n, "digest": d}

    if os.path.isdir(folder):
        walk(folder, "")
    return ents, bad


def _relation(docs: Mapping[str, Mapping[str, Any]], a: str, b: str) -> str:
    def chain(d: str) -> set[str]:
        seen: set[str] = set()
        cur = docs[d].get("previous")
        while cur and cur in docs and cur not in seen:
            seen.add(cur)
            cur = docs[cur].get("previous")
        return seen

    if a == b:
        return "same"
    if a in chain(b):
        return "descendant"
    if b in chain(a):
        return "ancestor"
    return "unrelated"


def _load(root: str, areas: AreaIndex, digest: str, dataset: str) -> tuple[dict, dict]:
    try:
        path = find_document(root, areas, digest)
        doc = read_snapshot(path)
    except (VerifyError, SnapshotError, OSError) as e:
        raise DiffError(str(e)) from None
    if doc.get("snapshot_version", 1) != 2:
        raise DiffError(f"{digest} is a version 1 snapshot; diff needs version 2")
    if doc["dataset"] != dataset:
        raise DiffError(f"{digest} belongs to dataset {doc['dataset']!r}, not {dataset!r}")
    try:
        return doc, load_entries(doc, os.path.dirname(path))
    except (OSError, ValueError, KeyError) as e:
        raise DiffError(f"cannot read entries of {digest}: {e}") from None


def _lineage_docs(root: str, areas: AreaIndex, dataset: str) -> dict[str, Mapping[str, Any]]:
    docs: dict[str, Mapping[str, Any]] = {}
    folder = snapshot_dir(root, areas)
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("dataset") == dataset and "digest" in d:
                docs[d["digest"]] = d
    return docs


def diff_dataset(
    root: str,
    structure: Mapping[str, Any] | AreaIndex | None,
    dataset: str,
    *,
    from_digest: str | None = None,
    to_digest: str | None = None,
    paths: Sequence[str] = (),
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    depth: str = "sha256",
) -> dict[str, Any]:
    """Compare two snapshots of one dataset, or a snapshot with the working copy (``to_digest="working"``).

    Parameters
    ----------
    root : str
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset : str
        Project-relative dataset folder.
    from_digest, to_digest : str, optional
        Snapshot digests. ``to`` defaults to the head, ``from`` to the ``previous`` of ``to`` (none: empty side);
        ``to`` may be ``"working"`` (``from`` then defaults to the head).
    depth : {"sha256", "size"}
        Hashing depth of the working copy; ignored between snapshots.
    paths, include, exclude : sequence of str
        Selection: path prefixes, and glob patterns on the whole path.

    Returns
    -------
    dict
        The ``diff_version`` 1 document.

    Raises
    ------
    DiffError
        On any error (exit status 3).
    """
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    dataset = dataset.strip("/")
    if depth not in ("size", "sha256"):
        raise DiffError(f"unknown depth {depth!r}; use size or sha256")
    working = to_digest == "working"
    head_digest = None
    if to_digest is None or (working and from_digest is None):
        try:
            head = find_document(root, areas, dataset)
        except VerifyError as e:
            raise DiffError(str(e)) from None
        head_digest = "sha256:" + os.path.basename(head)[: -len(".json")]
    sel = {"paths": list(paths), "include": list(include), "exclude": list(exclude)}
    absent: list[dict] = []
    unreadable: list[dict] = []
    if working:
        to_doc = None
        from_doc, old = _load(root, areas, from_digest or head_digest, dataset)
        new, bad = _scan_working(os.path.join(root, dataset), depth)
        old = {p: e for p, e in old.items() if _select(p, paths, include, exclude)}
        new = {p: e for p, e in new.items() if _select(p, paths, include, exclude)}
        unreadable = [{"path": p, "reason": r} for p, r in sorted(bad.items(), key=lambda t: _key(t[0]))
                      if _select(p, paths, include, exclude)]
        for u in unreadable:
            old.pop(u["path"], None)
        pre_changed: list[dict] = []
        pre_same = 0
        if depth == "size":
            for p in sorted(old.keys() & new.keys(), key=_key):
                if old[p]["size"] == new[p]["size"]:
                    pre_same += 1
                else:
                    pre_changed.append({"path": p, "from": {"size": old[p]["size"], "digest": old[p]["digest"]},
                                        "to": {"size": new[p]["size"], "digest": None}})
                del old[p], new[p]
        res = classify(old, new)
        res["changed"] = sorted(res["changed"] + pre_changed, key=lambda c: _key(c["path"]))
        res["unchanged"] += pre_same
        relation = None
    else:
        if to_digest is None:
            to_digest = head_digest
        to_doc, new = _load(root, areas, to_digest, dataset)
        prev = from_digest if from_digest is not None else to_doc["previous"]
        from_doc, old = (None, {}) if prev is None else _load(root, areas, prev, dataset)
        if from_doc is None:
            relation = None
        else:
            docs = _lineage_docs(root, areas, dataset)
            docs.setdefault(from_doc["digest"], from_doc)
            docs.setdefault(to_doc["digest"], to_doc)
            relation = _relation(docs, from_doc["digest"], to_doc["digest"])
        old = {p: e for p, e in old.items() if _select(p, paths, include, exclude)}
        new = {p: e for p, e in new.items() if _select(p, paths, include, exclude)}
        res = classify(old, new)
    ch = res["changed"]
    return {
        "diff_version": 1,
        "dataset": dataset,
        "from": {"snapshot": from_doc["digest"], "snapshot_version": 2} if from_doc else None,
        "to": {"working": True, "depth": depth} if working else {"snapshot": to_doc["digest"], "snapshot_version": 2},
        "relation": relation,
        "selection": sel,
        "counts": {"added": len(res["added"]), "removed": len(res["removed"]), "changed": len(ch),
                   "moved": len(res["moved"]), "unchanged": res["unchanged"], "absent": len(absent),
                   "unreadable": len(unreadable)},
        "bytes": {"added": sum(e["size"] for e in res["added"]),
                  "removed": sum(e["size"] for e in res["removed"]),
                  "changed_from": sum(c["from"]["size"] for c in ch),
                  "changed_to": sum(c["to"]["size"] for c in ch)},
        "added": res["added"],
        "removed": res["removed"],
        "changed": ch,
        "moved": res["moved"],
        "absent": absent,
        "unreadable": unreadable,
    }


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    x = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        x /= 1024
        if x < 1024 or unit == "TiB":
            return f"{x:.1f} {unit}"
    raise AssertionError  # pragma: no cover


def render_text(doc: Mapping[str, Any], stat: bool = False) -> str:
    """Render the text form (docs/DIFF.md 8.1); ``stat`` prints only the header and the summary."""
    frm = doc["from"]["snapshot"] if doc["from"] else "null"
    to = doc["to"]
    tol = f"working (depth {to['depth']})" if to.get("working") else to["snapshot"]
    head = f"diff {doc['dataset']} {frm} -> {tol}"
    lines = [head + (f", {doc['relation']}" if doc["relation"] else "")]
    if not stat:
        ents: list[tuple[str, str]] = []
        ents += [(e["path"], f"A {e['path']}") for e in doc["added"]]
        ents += [(e["path"], f"D {e['path']}") for e in doc["removed"]]
        ents += [(e["path"], f"M {e['path']} {e['from']['size']} -> {e['to']['size']}") for e in doc["changed"]]
        ents += [(e["to_path"], f"R {e['from_path']} -> {e['to_path']}") for e in doc["moved"]]
        ents += [(e["path"], f"- {e['path']}") for e in doc["absent"]]
        ents += [(e["path"], f"! {e['path']} {e['reason']}") for e in doc["unreadable"]]
        lines += [t for _, t in sorted(ents, key=lambda x: _key(x[0]))]
    c, b = doc["counts"], doc["bytes"]
    extra = ""
    if c["absent"] or c["unreadable"]:
        extra = f", {c['absent']} absent, {c['unreadable']} unreadable"
    lines.append(f"{c['added']} added, {c['removed']} removed, {c['changed']} changed, {c['moved']} moved, "
                 f"{c['unchanged']} unchanged{extra}; +{_size(b['added'])}, -{_size(b['removed'])}")
    return "\n".join(lines)
