"""Retention policy documents (docs/plans/VERSIONS.md section 2): records, lineage and the one-head rule.

This module writes and reads the policy documents only. It evaluates nothing (no kept, pinned or droppable
verdict per version) and guards no other command; those are later steps of the plan.

A policy is one document per revision, ``<record area>/retention/v1/<hex>.json``, ``<hex>`` the digest of the
document without ``digest``, ``previous`` and ``created`` (``merges`` is digested). The head of a scope is the
revision that no other revision names in ``previous`` or ``merges``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any

from . import schema as sch
from .workflow.areas import AreaIndex
from .workflow.execute import DEFAULT_RECORD_AREA, write_json_atomic
from .workflow.identity import digest_of

__all__ = [
    "RetentionError",
    "check_document",
    "effective_rules",
    "lineage",
    "pin_from_run",
    "pin_version",
    "read_policies",
    "retention_dir",
    "retention_problems",
    "scope_key",
    "set_policy",
    "show",
    "unpin_version",
]

RETENTION_DIR = os.path.join("retention", "v1")
SNAPSHOT_V2 = os.path.join("snapshots", "v2")
REASON_MAX = 200
_UNDIGESTED = ("digest", "previous", "created")
_UNSET: Any = object()
_EMPTY = {"keep_last": None, "keep_days": None, "hold_copies": 1, "pins": []}


class RetentionError(ValueError):
    """Raised when a policy document cannot be written or read, or an operation is refused."""


# --------------------------------------------------------------------------- helpers


def _areas(structure: Mapping[str, Any] | AreaIndex | None) -> AreaIndex:
    return structure if isinstance(structure, AreaIndex) else AreaIndex(structure)


def retention_dir(root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None = None) -> str:
    """Return the folder of policy documents, ``<record area>/retention/v1`` (section 2.3).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``; names the record area.

    Returns
    -------
    str
        Absolute folder path.
    """
    return os.path.join(os.fspath(root), _areas(structure).record_path() or DEFAULT_RECORD_AREA, RETENTION_DIR)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hex(digest: str) -> str:
    return digest.split(":", 1)[1]


def scope_key(scope: Mapping[str, Any]) -> str:
    """Return a stable text key for a scope, ``dataset:<folder>`` or ``area:<name>``."""
    kind = scope.get("kind")
    return f"{kind}:{scope.get(str(kind))}"


def _bad_folder(p: str) -> bool:
    parts = p.split("/")
    return not p or p.startswith("/") or ".." in parts or "." in parts or "" in parts


def _dataset_scope(areas: AreaIndex, dataset: str) -> dict[str, str]:
    p = str(dataset).replace(os.sep, "/").strip("/")
    if _bad_folder(p):
        raise RetentionError(f"dataset must be a project-relative folder: {dataset!r}")
    if areas.area_of(p) is None:
        raise RetentionError(f"unknown dataset: {p!r} lies in no area")
    return {"kind": "dataset", "dataset": p}


def _area_scope(areas: AreaIndex, area: str) -> dict[str, str]:
    if area not in areas.areas:
        raise RetentionError(f"unknown area: {area!r}")
    return {"kind": "area", "area": area}


def _scope_arg(areas: AreaIndex, dataset: str | None, area: str | None) -> dict[str, str]:
    if (dataset is None) == (area is None):
        raise RetentionError("name exactly one of a dataset or --area")
    if dataset is not None:
        return _dataset_scope(areas, dataset)
    return _area_scope(areas, str(area))


def _digest_of_doc(doc: Mapping[str, Any]) -> str:
    return digest_of({k: v for k, v in doc.items() if k not in _UNDIGESTED})


def _ensure_digest(value: Any, what: str) -> str:
    if not (isinstance(value, str) and value.startswith("sha256:") and len(value) == 71
            and all(c in "0123456789abcdef" for c in value[7:])):
        raise RetentionError(f"{what} is not a digest: {value!r}")
    return value


# --------------------------------------------------------------------------- documents


def check_document(doc: Any, filename: str | None = None) -> list[str]:
    """Check one policy document against the schema and the rules the schema cannot express.

    Parameters
    ----------
    doc : object
        Parsed JSON.
    filename : str or None
        File name (``<hex>.json``); when given it must equal the digest.

    Returns
    -------
    list of str
        One message per problem; empty when valid.
    """
    errs = sch.validate(doc, sch.load_bundled("retention"))
    if errs:
        return errs
    out: list[str] = []
    if doc["digest"] != _digest_of_doc(doc):
        out.append("digest does not match the document")
    if filename is not None and filename != _hex(doc["digest"]) + ".json":
        out.append(f"file name {filename!r} does not match the digest")
    pins = doc["pins"]
    snaps = [p["snapshot"] for p in pins]
    if snaps != sorted(snaps):
        out.append("pins are not sorted by snapshot")
    if len(set(snaps)) != len(snaps):
        out.append("pins name a snapshot twice")
    if pins and doc["scope"]["kind"] != "dataset":
        out.append("pins are allowed only in a dataset scope")
    for p in pins:
        r = p["reason"]
        if len(r) > REASON_MAX:
            out.append(f"pin {p['snapshot']}: reason is longer than {REASON_MAX} characters")
        if "/" in r or "\\" in r:
            out.append(f"pin {p['snapshot']}: reason must not contain a path")
    if doc["scope"]["kind"] == "dataset" and _bad_folder(doc["scope"]["dataset"]):
        out.append(f"scope dataset is not a project-relative folder: {doc['scope']['dataset']!r}")
    return out


def read_policies(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None = None
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    """Read every policy document, grouped by scope.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.

    Returns
    -------
    tuple
        ``(scopes, problems)``: ``scopes`` maps a scope key to ``{digest: document}`` for the valid documents;
        ``problems`` lists unreadable or invalid files (the file name leads each message).
    """
    folder = retention_dir(root, structure)
    scopes: dict[str, dict[str, dict[str, Any]]] = {}
    problems: list[str] = []
    if not os.path.isdir(folder):
        return scopes, problems
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if not os.path.isfile(p) or ".tmp-" in f:
            continue
        if not f.endswith(".json"):
            problems.append(f"{f}: not a policy document")
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            problems.append(f"{f}: cannot read: {e}")
            continue
        errs = check_document(doc, f)
        if errs:
            problems.extend(f"{f}: {e}" for e in errs)
            continue
        scopes.setdefault(scope_key(doc["scope"]), {})[doc["digest"]] = doc
    for key, revs in scopes.items():
        for d, doc in revs.items():
            for k in ("previous", "merges"):
                ref = doc.get(k)
                if ref is not None and ref not in revs:
                    problems.append(f"{_hex(d)}.json: {k} {ref} names no revision of {key}")
    return scopes, problems


def _heads(revs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    named = {r for doc in revs.values() for r in (doc.get("previous"), doc.get("merges")) if r}
    return sorted(d for d in revs if d not in named)


def _load_all(root: str, areas: AreaIndex) -> dict[str, dict[str, dict[str, Any]]]:
    scopes, problems = read_policies(root, areas)
    if problems:  # an invalid document could hide a head, so nothing is written or shown past it
        raise RetentionError("invalid policy document(s): " + "; ".join(problems))
    return scopes


def _single_head(revs: Mapping[str, Mapping[str, Any]], key: str) -> str | None:
    heads = _heads(revs)
    if len(heads) > 1:
        raise RetentionError(
            f"{key} has {len(heads)} policy heads ({', '.join(heads)}); write a revision that merges them "
            "with 'stradler retention set --merge'")
    return heads[0] if heads else None


# --------------------------------------------------------------------------- lineage


def lineage(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, dataset: str
) -> list[dict[str, Any]]:
    """List the version 2 snapshot documents of a dataset, newest first when they form one chain.

    Only ``dataset``, ``digest``, ``previous`` and ``created`` are read; no entry stream or node is opened.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset : str
        Project-relative dataset folder.

    Returns
    -------
    list of dict
        ``{digest, previous, created}``: the chain from the head back, then any document not on it (by digest).
    """
    areas = _areas(structure)
    folder = os.path.join(os.fspath(root), areas.record_path() or DEFAULT_RECORD_AREA, SNAPSHOT_V2)
    docs: dict[str, dict[str, Any]] = {}
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if f.endswith(".json") and os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("dataset") == dataset and isinstance(d.get("digest"), str):
                    docs[d["digest"]] = {"digest": d["digest"], "previous": d.get("previous"),
                                         "created": d.get("created")}
    named = {d["previous"] for d in docs.values() if d["previous"]}
    heads = sorted(d for d in docs if d not in named)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur = heads[0] if heads else None
    while cur in docs and cur not in seen:
        seen.add(cur)
        out.append(docs[cur])
        cur = docs[cur]["previous"]
    out.extend(docs[d] for d in sorted(docs) if d not in seen)
    return out


def _in_lineage(root: str, areas: AreaIndex, dataset: str, snapshot: str) -> None:
    if snapshot not in {d["digest"] for d in lineage(root, areas, dataset)}:
        raise RetentionError(f"{snapshot} is not a version of dataset {dataset!r}")


# --------------------------------------------------------------------------- effective rules and show


def _union(docs: list[Mapping[str, Any]]) -> dict[str, Any]:
    def widest(key: str) -> int | None:
        vals = [d[key] for d in docs if d[key] is not None]
        return max(vals) if vals else None

    pins: dict[str, Any] = {}
    for d in docs:
        for p in d["pins"]:
            pins.setdefault(p["snapshot"], dict(p))
    return {
        "keep_last": widest("keep_last"), "keep_days": widest("keep_days"),
        "hold_copies": max([d.get("hold_copies", 1) for d in docs] or [1]),
        "pins": [pins[k] for k in sorted(pins)], "policies": [d["digest"] for d in docs],
    }


def effective_rules(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, dataset: str
) -> dict[str, Any]:
    """Combine the area policy and the dataset policy of a dataset (union, section 2.1).

    The union keeps a version when either policy keeps it, so each numeric limit is the larger of the two and a
    ``null`` rule defers to the other policy. This only combines the stated rules; it evaluates no version.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset : str
        Project-relative dataset folder.

    Returns
    -------
    dict
        ``keep_last``, ``keep_days``, ``hold_copies``, ``pins`` and the digests of the policies used
        (``policies``).
    """
    areas = _areas(structure)
    scope = _dataset_scope(areas, dataset)
    hit = areas.area_of(scope["dataset"])
    scopes = _load_all(os.fspath(root), areas)
    docs: list[dict[str, Any]] = []
    for sc in ([_area_scope(areas, hit[0])] if hit else []) + [scope]:
        key = scope_key(sc)
        revs = scopes.get(key, {})
        head = _single_head(revs, key)
        if head:
            docs.append(revs[head])
    return _union(docs)


def show(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, *,
    dataset: str | None = None, area: str | None = None,
) -> dict[str, Any]:
    """Describe the head policy of a scope, its revision lineage and, for a dataset, the effective rules.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset, area : str or None
        Exactly one names the scope.

    Returns
    -------
    dict
        ``scope``, ``head`` (document or ``None``), ``revisions`` (newest first) and, for a dataset scope,
        ``effective``.

    Raises
    ------
    RetentionError
        For an unknown dataset or area, two policy heads, or an invalid document.
    """
    areas = _areas(structure)
    root = os.fspath(root)
    scope = _scope_arg(areas, dataset, area)
    key = scope_key(scope)
    revs = _load_all(root, areas).get(key, {})
    head = _single_head(revs, key)
    chain: list[dict[str, Any]] = []
    cur = head
    while cur is not None and cur in revs and cur not in {c["digest"] for c in chain}:
        d = revs[cur]
        chain.append({"digest": cur, "previous": d["previous"], "created": d["created"],
                      **({"merges": d["merges"]} if "merges" in d else {})})
        cur = d["previous"]
    out: dict[str, Any] = {"scope": scope, "head": revs[head] if head else None, "revisions": chain}
    if scope["kind"] == "dataset":
        out["effective"] = effective_rules(root, areas, scope["dataset"])
    return out


# --------------------------------------------------------------------------- writing


def _build(scope: Mapping[str, Any], rules: Mapping[str, Any], previous: str | None, created: str,
           merges: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "retention_version": 1, "scope": dict(scope), "keep_last": rules["keep_last"],
        "keep_days": rules["keep_days"], "hold_copies": rules["hold_copies"],
        "pins": sorted((dict(p) for p in rules["pins"]), key=lambda p: p["snapshot"]),
        "previous": previous, "created": created,
    }
    if merges is not None:
        doc["merges"] = merges
    doc["digest"] = _digest_of_doc(doc)
    errs = check_document(doc)
    if errs:
        raise RetentionError("invalid policy: " + "; ".join(errs))
    return doc


def _commit(folder: str, doc: dict[str, Any], head_doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Write ``doc`` unless it equals the head; refuse a digest that an earlier revision already holds."""
    if head_doc is not None and head_doc["digest"] == doc["digest"]:
        return {"document": dict(head_doc), "written": False}
    path = os.path.join(folder, _hex(doc["digest"]) + ".json")
    if os.path.exists(path):
        raise RetentionError(
            f"the rules equal an earlier revision of this scope ({doc['digest']}); a revision is named by its "
            "rules, so it cannot be written again as a newer one")
    write_json_atomic(path, doc)
    return {"document": doc, "written": True}


def _int_or_none(name: str, value: Any, low: int) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise RetentionError(f"{name} must be an integer of at least {low}")
    return value


def set_policy(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, *,
    dataset: str | None = None, area: str | None = None,
    keep_last: Any = _UNSET, keep_days: Any = _UNSET, hold_copies: Any = _UNSET,
    merge: bool = False, created: str | None = None,
) -> dict[str, Any]:
    """Write a new revision of a scope's policy (section 2.4, ``retention set``).

    A first revision starts from ``keep_last`` null, ``keep_days`` null, ``hold_copies`` 1 and no pins; a later one
    from the head. ``None`` for ``keep_last`` or ``keep_days`` clears the rule; omitted leaves it.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset, area : str or None
        Exactly one names the scope.
    keep_last, keep_days, hold_copies
        New values; omitted keeps the current one.
    merge : bool
        Resolve two heads: ``previous`` names the first head, ``merges`` the second, and the rules are the union
        of both, then the options above are applied.
    created : str or None
        Timestamp ``YYYY-MM-DDTHH:MM:SSZ``; the current UTC time when ``None``.

    Returns
    -------
    dict
        ``{"document", "written"}``; ``written`` is false when the rules equal the head.

    Raises
    ------
    RetentionError
        For an unknown scope, two heads without ``merge``, ``merge`` without two heads, or invalid values.
    """
    areas = _areas(structure)
    root = os.fspath(root)
    scope = _scope_arg(areas, dataset, area)
    key = scope_key(scope)
    revs = _load_all(root, areas).get(key, {})
    heads = _heads(revs)
    merges = None
    head_doc = None
    if merge:
        if len(heads) != 2:
            raise RetentionError(f"--merge needs exactly two heads; {key} has {len(heads)}")
        rules = _union([revs[h] for h in heads])
        previous, merges = heads[0], heads[1]
    else:
        previous = _single_head(revs, key)
        head_doc = revs[previous] if previous else None
        rules = _union([head_doc]) if head_doc else dict(_EMPTY)
    if keep_last is not _UNSET:
        rules["keep_last"] = _int_or_none("keep_last", keep_last, 1)
    if keep_days is not _UNSET:
        rules["keep_days"] = _int_or_none("keep_days", keep_days, 0)
    if hold_copies is not _UNSET:
        if hold_copies is None:
            raise RetentionError("hold_copies cannot be cleared")
        rules["hold_copies"] = _int_or_none("hold_copies", hold_copies, 1)
    doc = _build(scope, rules, previous, created or _now(), merges)
    return _commit(retention_dir(root, areas), doc, head_doc)


def _check_reason(reason: Any) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise RetentionError("a pin needs a reason")
    if len(reason) > REASON_MAX:
        raise RetentionError(f"reason is longer than {REASON_MAX} characters")
    if "/" in reason or "\\" in reason:
        raise RetentionError("reason must not contain a path")
    return reason


def _head_state(root: str, areas: AreaIndex, scope: Mapping[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    key = scope_key(scope)
    revs = _load_all(root, areas).get(key, {})
    head = _single_head(revs, key)
    return head, (revs[head] if head else None)


def _with_pins(head_doc: Mapping[str, Any] | None, pins: list[dict[str, Any]]) -> dict[str, Any]:
    base = _union([head_doc]) if head_doc else dict(_EMPTY)
    return {**base, "pins": pins}


def pin_version(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, dataset: str, snapshot: str,
    reason: str, *, created: str | None = None,
) -> dict[str, Any]:
    """Pin a version of a dataset by snapshot digest (section 2.4, ``retention pin``).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset : str
        Project-relative dataset folder.
    snapshot : str
        Content digest of a version 2 snapshot document of the dataset.
    reason : str
        Free text, at most 200 characters, no paths.
    created : str or None
        Timestamp of the revision and of the pin; the current UTC time when ``None``.

    Returns
    -------
    dict
        ``{"document", "written"}``.

    Raises
    ------
    RetentionError
        For a digest outside the dataset's lineage, an already pinned digest, a bad reason or two heads.
    """
    areas = _areas(structure)
    root = os.fspath(root)
    scope = _dataset_scope(areas, dataset)
    snapshot = _ensure_digest(snapshot, "snapshot")
    reason = _check_reason(reason)
    _in_lineage(root, areas, scope["dataset"], snapshot)
    head, head_doc = _head_state(root, areas, scope)
    now = created or _now()
    pins = [dict(p) for p in (head_doc["pins"] if head_doc else [])]
    if any(p["snapshot"] == snapshot for p in pins):
        raise RetentionError(f"{snapshot} is already pinned")
    pins.append({"snapshot": snapshot, "snapshot_version": 2, "reason": reason, "added": now})
    doc = _build(scope, _with_pins(head_doc, pins), head, now)
    return _commit(retention_dir(root, areas), doc, head_doc)


def unpin_version(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, dataset: str, snapshot: str, *,
    created: str | None = None,
) -> dict[str, Any]:
    """Remove a pin (section 2.4, ``retention unpin``) by writing a new revision.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    dataset : str
        Project-relative dataset folder.
    snapshot : str
        Digest of the pinned version.
    created : str or None
        Timestamp of the revision; the current UTC time when ``None``.

    Returns
    -------
    dict
        ``{"document", "written"}``.

    Raises
    ------
    RetentionError
        When the digest is not pinned, for two heads, or when the resulting rules equal an earlier revision (a
        revision is named by the digest of its rules, so it cannot be written twice).
    """
    areas = _areas(structure)
    root = os.fspath(root)
    scope = _dataset_scope(areas, dataset)
    snapshot = _ensure_digest(snapshot, "snapshot")
    head, head_doc = _head_state(root, areas, scope)
    pins = [dict(p) for p in (head_doc["pins"] if head_doc else [])]
    rest = [p for p in pins if p["snapshot"] != snapshot]
    if len(rest) == len(pins):
        raise RetentionError(f"{snapshot} is not pinned")
    doc = _build(scope, _with_pins(head_doc, rest), head, created or _now())
    return _commit(retention_dir(root, areas), doc, head_doc)


def pin_from_run(
    root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None, run: str | os.PathLike, *,
    reason: str | None = None, created: str | None = None,
) -> dict[str, Any]:
    """Pin every input version a run record names (section 5.1, Decision 13).

    The record is ``run.json`` of a run folder (the folder or the file may be given). Each entry of
    ``inputs.datasets`` that carries a ``snapshot`` digest is one input version of dataset ``path``. Every
    digest is checked against its lineage before any revision is written.

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.
    run : str or os.PathLike
        Run folder or its ``run.json``.
    reason : str or None
        Reason of each pin; default ``input of run <run_id>``.
    created : str or None
        Timestamp; the current UTC time when ``None``.

    Returns
    -------
    dict
        ``{"run_id", "pins": [{dataset, snapshot, written}]}``; an already pinned version is listed with
        ``written`` false.

    Raises
    ------
    RetentionError
        When the record is unreadable or names no input snapshot, or a pin is refused.
    """
    areas = _areas(structure)
    root = os.fspath(root)
    path = os.fspath(run)
    if os.path.isdir(path):
        path = os.path.join(path, "run.json")
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as e:
        raise RetentionError(f"cannot read run record {path}: {e}") from None
    ds = rec.get("inputs", {}).get("datasets") if isinstance(rec, dict) else None
    items: set[tuple[str, str]] = set()
    for e in ds if isinstance(ds, list) else []:
        if isinstance(e, dict) and "snapshot" in e:
            items.add((_dataset_scope(areas, str(e.get("path", "")))["dataset"], _ensure_digest(e["snapshot"], "snapshot")))
    if not items:
        raise RetentionError(f"run record {path} names no input snapshot (inputs.datasets[].snapshot)")
    run_id = str(rec.get("run_id", ""))
    why = _check_reason(reason if reason is not None else f"input of run {run_id}")
    for d, s in items:
        _in_lineage(root, areas, d, s)
    done = []
    for d, s in sorted(items):
        try:
            written = pin_version(root, areas, d, s, why, created=created)["written"]
        except RetentionError as e:
            if "already pinned" not in str(e):
                raise
            written = False
        done.append({"dataset": d, "snapshot": s, "written": written})
    return {"run_id": run_id, "pins": done}


# --------------------------------------------------------------------------- validate


def retention_problems(root: str | os.PathLike, structure: Mapping[str, Any] | AreaIndex | None) -> list[str]:
    """Validate the policy documents of a project (shape, scopes, pins in lineage, one head per scope).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex or None
        Parsed ``structure.toml``.

    Returns
    -------
    list of str
        One message per problem, each starting ``retention:``; empty when there is no policy or all are valid.
    """
    areas = _areas(structure)
    root = os.fspath(root)
    scopes, problems = read_policies(root, areas)
    out = [f"retention: {m}" for m in problems]
    for key, revs in sorted(scopes.items()):
        heads = _heads(revs)
        if len(heads) > 1:
            out.append(f"retention: {key} has {len(heads)} policy heads ({', '.join(heads)})")
        kind, _, name = key.partition(":")
        if kind == "area" and name not in areas.areas:
            out.append(f"retention: {key}: unknown area")
        if kind == "dataset":
            if areas.area_of(name) is None:
                out.append(f"retention: {key}: lies in no area")
            known = {d["digest"] for d in lineage(root, areas, name)}
            for head in heads:
                for p in revs[head]["pins"]:
                    if p["snapshot"] not in known:
                        out.append(f"retention: {key}: pin {p['snapshot']} is not a version of the dataset")
    return out
