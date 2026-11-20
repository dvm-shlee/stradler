"""Declared copies of an area (``[replicas.<area>]``, config 1.1.0) and their checks (SNAPSHOTS.md section 7.6).

Each check is the step-2 verify (:mod:`stradler.verify`) of one latest snapshot against one declared store. A local
store (a ``path`` in ``local.toml``) is read by the core; any other store is checked through the ``digest`` operation
of its store add-on (ADDONS.md section 8), so no file has to be downloaded.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .addons import Addon, AddonError, load_addons
from .project import Project
from .snapshot import SnapshotError, read_snapshot, snapshot_dir
from .verify import (
    CATEGORIES,
    VerifyError,
    _docs,
    load_entries,
    verify_dataset,
    write_check_record,
)
from .workflow.areas import AreaIndex

__all__ = ["ReplicaError", "declared", "exit_code", "latest_snapshots", "replica_status", "verify_replicas"]

CHUNK = 500
NOT_SUPPORTED = "verify not supported"


class ReplicaError(Exception):
    """Raised when replica checks cannot be made for a request (exit status 3)."""


def declared(config: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Return ``[replicas]`` of a parsed ``config.toml`` (``{}`` for config 1.0.0 or without the table)."""
    r = (config or {}).get("replicas")
    return {k: v for k, v in r.items() if isinstance(v, dict)} if isinstance(r, dict) else {}


def latest_snapshots(root: str, structure: Mapping[str, Any] | None, area: str) -> tuple[list[dict], list[str]]:
    """Find the latest (head) version 2 snapshot of each dataset that lies in ``area``.

    Returns
    -------
    tuple
        Documents sorted by dataset, and one message per dataset that has no single head.
    """
    areas = AreaIndex(structure)
    by_ds: dict[str, dict[str, dict]] = {}
    for _, d in _docs(snapshot_dir(root, areas)):
        ds = d.get("dataset")
        hit = areas.area_of(ds) if isinstance(ds, str) else None
        if hit and hit[0] == area and "digest" in d:
            by_ds.setdefault(ds, {})[d["digest"]] = d
    heads, problems = [], []
    for ds in sorted(by_ds):
        docs = by_ds[ds]
        named = {d.get("previous") for d in docs.values() if d.get("previous")}
        h = sorted(k for k in docs if k not in named)
        if len(h) == 1:
            heads.append(docs[h[0]])
        else:
            problems.append(f"dataset {ds!r} has {len(h)} heads")
    return heads, problems


def _addon_for(project: Project, store: str) -> Addon | None:
    st = ((project.config or {}).get("stores") or {}).get(store) or {}
    try:
        addons = load_addons(project.root)
    except AddonError:
        return None
    a = addons.get(st.get("addon"))
    if a is None:
        return None
    loc = ((project.local or {}).get("addons") or {}).get(a.name) or {}
    if isinstance(loc.get("settings"), dict):
        a.settings = {**a.settings, **loc["settings"]}
    return a


def _remote_compare(addon: Addon, store: str, local_store: Mapping[str, Any], dataset: str,
                    entries: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Compare snapshot entries with the ``digest`` answers of a store add-on; ``added`` is not observable."""
    counts = {c: 0 for c in CATEGORIES}
    paths = sorted(entries)
    for i in range(0, len(paths), CHUNK):
        chunk = paths[i:i + CHUNK]
        res = addon.call("digest", {"store": store, "store_settings": dict(local_store),
                                    "paths": [f"{dataset}/{p}" for p in chunk]})
        got = {}
        for f in res.get("files") or []:
            if isinstance(f, dict) and isinstance(f.get("path"), str):
                got[f["path"]] = f
        for p in chunk:
            f = got.get(f"{dataset}/{p}")
            e = entries[p]
            if f is None:
                counts["missing"] += 1
                continue
            sha = str(f.get("sha256", ""))
            sha = sha if sha.startswith("sha256:") else "sha256:" + sha
            counts["match" if (sha, f.get("size")) == (e["digest"], e["size"]) else "changed"] += 1
    return counts


def _check(project: Project, areas: AreaIndex, store: str, doc: dict) -> dict[str, Any]:
    root, local = str(project.root), project.local or {}
    base = {"store": store, "dataset": doc["dataset"], "snapshot": doc["digest"]}
    spath = ((local.get("stores") or {}).get(store) or {}).get("path")
    if isinstance(spath, str):
        r = verify_dataset(root, project.structure, doc["digest"], store=store, local=local)
        return {**base, "result": r["result"], "counts": r.get("counts"), "record": r["record"]}
    addon = _addon_for(project, store)
    if addon is None or addon.kind not in ("store", "remote"):
        return {**base, "result": "not_supported", "message": f"{NOT_SUPPORTED} for store {store!r}"}
    folder = os.path.join(snapshot_dir(root, areas))
    try:
        docpath = next(p for p, d in _docs(folder) if d.get("digest") == doc["digest"])
        entries = load_entries(read_snapshot(docpath), os.path.dirname(docpath))
    except (StopIteration, SnapshotError, OSError, ValueError) as exc:
        raise ReplicaError(f"snapshot document of {doc['dataset']!r} is not valid: {exc}") from None
    try:
        counts = _remote_compare(addon, store, (local.get("stores") or {}).get(store) or {}, doc["dataset"], entries)
    except AddonError as exc:
        if exc.code == "unsupported_operation":
            return {**base, "result": "not_supported", "message": f"{NOT_SUPPORTED} for store {store!r}"}
        path = write_check_record(root, areas, store, doc, "unreachable", None)
        return {**base, "result": "unreachable", "counts": None, "record": path}
    result = "match" if all(counts[c] == 0 for c in CATEGORIES[1:]) else "differs"
    return {**base, "result": result, "counts": counts,
            "record": write_check_record(root, areas, store, doc, result, counts)}


def verify_replicas(project: Project, area: str) -> dict[str, Any]:
    """Check the latest snapshots of ``area`` against every declared store; one check record per pair.

    Parameters
    ----------
    project : Project
        A loaded, valid project.
    area : str
        Area name with a ``[replicas.<area>]`` declaration.

    Returns
    -------
    dict
        ``area``, ``checks`` (per store and snapshot: ``store``, ``dataset``, ``snapshot``, ``result`` which is
        ``match``, ``differs``, ``unreachable`` or ``not_supported``, ``counts``, ``record`` or ``message``) and
        ``problems``.

    Raises
    ------
    ReplicaError
        When the area has no declaration.
    """
    decl = declared(project.config).get(area)
    if decl is None:
        raise ReplicaError(f"no [replicas.{area}] declaration")
    areas = AreaIndex(project.structure)
    heads, problems = latest_snapshots(str(project.root), project.structure, area)
    checks: list[dict[str, Any]] = []
    for store in decl["stores"]:
        for doc in heads:
            try:
                checks.append(_check(project, areas, store, doc))
            except VerifyError as exc:
                raise ReplicaError(str(exc)) from None
    return {"area": area, "checks": checks, "problems": problems}




def exit_code(out: Mapping[str, Any]) -> int:
    """Exit status: 1 any difference, else 2 any unreachable store, else 3 any unsupported store, else 0."""
    results = {c["result"] for c in out["checks"]}
    if "differs" in results:
        return 1
    if "unreachable" in results:
        return 2
    if "not_supported" in results or out["problems"]:
        return 3
    return 0


def _records(root: str, areas: AreaIndex) -> list[dict]:
    folder = os.path.join(root, areas.record_path() or "records", "checks")
    out = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        d = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and isinstance(d.get("time"), str):
                    out.append(d)
    return out


def replica_status(project: Project) -> list[dict[str, Any]]:
    """Summarize the declared replicas per (area, store) from the recorded checks; nothing is checked here.

    ``last_check`` and ``result`` cover the area's latest snapshots: ``result`` is ``differs`` if any latest
    check differs, else ``unreachable`` if any is unreachable, else ``partial`` if some snapshot has no check for
    the store, else ``match``; both are ``None`` when no latest snapshot has a check. ``min_copies_met`` tells whether
    at least ``min_copies`` declared stores have result ``match`` (``None`` without ``min_copies``).
    """
    root, areas = str(project.root), AreaIndex(project.structure)
    recs = _records(root, areas)
    out = []
    for area in sorted(declared(project.config)):
        decl = declared(project.config)[area]
        heads, _ = latest_snapshots(root, project.structure, area)
        rows = []
        for store in decl.get("stores", []):
            latest = []
            for doc in heads:
                mine = [r for r in recs if r.get("store") == store and r.get("snapshot") == doc["digest"]]
                latest.append(max(mine, key=lambda r: r["time"]) if mine else None)
            seen = [r for r in latest if r]
            if not seen:
                result, last = None, None
            else:
                rs = {r["result"] for r in seen}
                result = ("differs" if "differs" in rs else "unreachable" if "unreachable" in rs
                          else "partial" if len(seen) < len(latest) else "match")
                last = max(r["time"] for r in seen)
            rows.append({"area": area, "store": store, "declared": True, "last_check": last, "result": result})
        mc = decl.get("min_copies")
        matched = sum(1 for r in rows if r["result"] == "match")
        for r in rows:
            r["min_copies"] = mc
            r["min_copies_met"] = None if mc is None else matched >= mc
        out.extend(rows)
    return out
