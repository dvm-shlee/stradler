"""``stradler adopt``: take an existing directory in as a project, or copy files into one.

In-place mode (:func:`propose`, :func:`adopt_in_place`) inspects a directory and writes only ``stradler.json``
and ``.stradler/``; it never touches an existing file. Copy mode (:func:`adopt_plan`) copies the files named by
a plan into a project, verifying size and sha256 after each copy, and never modifies a source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import threading
import time
from pathlib import Path
from typing import Any

from . import schema as sch
from .init import SPEC_VERSION, InitError, _materialize, load_template
from .project import CONFIG_DIR, MARKER, find_root, load_project
from .snapshot import SnapshotError, file_digest
from .workflow.areas import AreaIndex

__all__ = ["AdoptError", "adopt_in_place", "adopt_plan", "dry_run_plan", "load_plan", "parse_rate", "propose"]

_NAME = re.compile(r"[^A-Za-z0-9_-]")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TMP_SUFFIX = ".stradler-part"
_CHUNK = 1 << 20
_clock = time.monotonic
_sleep = time.sleep
_RATE = re.compile(r"^(\d+(?:\.\d+)?)([KMGkmg]?)$")


class AdoptError(InitError):
    """Raised when a directory cannot be adopted or a copy plan cannot be carried out."""


# --------------------------------------------------------------------------- in place


def _default_area(folder: str) -> dict[str, str]:
    return {"role": "input", "path": folder, "mutability": "immutable", "grade": "private"}


def propose(directory: str | Path, template: str | Path | None = None, project_id: str | None = None) -> dict[str, Any]:
    """Inspect a directory and propose the project files. Nothing is written.

    One area is proposed per top-level folder (hidden folders are skipped) with role ``input``, mutability
    ``immutable`` and grade ``private``. With a template, the template's areas and config are used as they are
    and only folders no template area covers get the default area.

    Returns
    -------
    dict
        ``{"root", "project_id", "marker", "config", "structure", "template"}``; ``template`` is ``None`` or
        the template record.

    Raises
    ------
    AdoptError
        If the directory is missing, already holds a project, or there is nothing to propose.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise AdoptError(f"not a directory: {root}")
    if (root / MARKER).exists() or (root / CONFIG_DIR).exists():
        raise AdoptError(f"{root} already holds a stradler project")
    areas: dict[str, Any] = {}
    cfg: dict[str, Any] = {}
    tpl: dict[str, str] | None = None
    if template is not None:
        doc, digest = load_template(template)
        areas = {k: dict(v) if isinstance(v, dict) else v for k, v in doc["areas"].items()}
        if isinstance(doc.get("config"), dict):
            cfg = sch.coerce(dict(doc["config"]), sch.load_bundled("config"))
        tpl = {"name": doc["name"], "version": doc["version"], "digest": digest}
    covered = {str(a["path"]).strip("/") for a in areas.values() if isinstance(a, dict) and "path" in a}
    for entry in sorted(os.listdir(root), key=lambda n: n.encode("utf-8", "surrogateescape")):
        if entry.startswith(".") or not (root / entry).is_dir() or (root / entry).is_symlink():
            continue
        if entry in covered:
            continue
        name = _NAME.sub("_", entry) or "area"
        base, i = name, 2
        while name in areas:
            name, i = f"{base}_{i}", i + 1
        areas[name] = _default_area(entry)
    if not areas:
        raise AdoptError(f"{root} has no top-level folder to propose an area for")
    pid = project_id or root.name
    if template is not None and not project_id:
        pid = doc.get("project_id") or pid
    return {
        "root": str(root),
        "project_id": pid,
        "marker": {"marker": "stradler-project", "spec_version": SPEC_VERSION, "project_id": pid, "config_dir": CONFIG_DIR},
        "config": {"schema": "config@1.0.0", **cfg},
        "structure": {"schema": "structure@1.1.0", "areas": areas},
        "template": tpl,
    }


def adopt_in_place(directory: str | Path, template: str | Path | None = None, project_id: str | None = None) -> Path:
    """Make an existing directory a project, writing only ``stradler.json`` and ``.stradler/``.

    No existing file is modified and no folder is created. The result is validated; on failure the written
    files are removed again and the directory is left as it was.

    Returns
    -------
    pathlib.Path
        The project root.
    """
    prop = propose(directory, template, project_id)
    root = Path(prop["root"])
    doc = {"areas": prop["structure"]["areas"], "config": {k: v for k, v in prop["config"].items() if k != "schema"}}
    tpl = prop["template"]
    if tpl:
        doc.update(name=tpl["name"], version=tpl["version"])
    _materialize(root, doc, tpl["digest"] if tpl else "", prop["project_id"],
                 make_areas=False, write_template=tpl is not None, keep_root=True,
                 structure_schema=prop["structure"]["schema"])
    return root


# --------------------------------------------------------------------------- copy mode


def load_plan(path: str | Path) -> list[dict[str, Any]]:
    """Read and check a copy plan: a JSON list of ``{source, dest, size, digest}``."""
    p = Path(path)
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise AdoptError(f"cannot read plan {p}: {e}") from e
    if not isinstance(plan, list):
        raise AdoptError("plan must be a JSON list")
    seen: set[str] = set()
    for i, e in enumerate(plan):
        where = f"plan[{i}]"
        if not isinstance(e, dict) or set(e) != {"source", "dest", "size", "digest"}:
            raise AdoptError(f"{where}: needs exactly source, dest, size and digest")
        if not isinstance(e["source"], str) or not e["source"]:
            raise AdoptError(f"{where}: source must be a non-empty string")
        d = e["dest"]
        if not isinstance(d, str) or not d or d.startswith("/") or any(s in ("", ".", "..") for s in d.split("/")):
            raise AdoptError(f"{where}: dest must be a relative path without empty, '.' or '..' segments")
        if d.split("/")[0] in (CONFIG_DIR, MARKER) or d.endswith(TMP_SUFFIX):
            raise AdoptError(f"{where}: dest is reserved")
        if not isinstance(e["size"], int) or isinstance(e["size"], bool) or e["size"] < 0:
            raise AdoptError(f"{where}: size must be a non-negative integer")
        if not isinstance(e["digest"], str) or not _DIGEST.match(e["digest"]):
            raise AdoptError(f"{where}: digest must be sha256:<64 hex digits>")
        if d in seen:
            raise AdoptError(f"{where}: dest listed twice")
        seen.add(d)
    return plan


def _verify(path: Path, size: int, digest: str) -> bool:
    try:
        d, n = file_digest(path)
    except (OSError, SnapshotError):
        return False
    return n == size and d == digest


class _Stop(Exception):
    """Internal: a stop signal arrived while a file was being copied."""


def parse_rate(text: str) -> int:
    """Parse a rate in bytes per second with an optional K, M or G suffix (powers of 1024)."""
    m = _RATE.match(str(text).strip())
    if not m or float(m.group(1)) <= 0:
        raise AdoptError(f"invalid rate: {text!r} (use a positive number with optional K, M or G)")
    mult = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}[m.group(2).lower()]
    return max(1, int(float(m.group(1)) * mult))


def _copy_one(source: Path, tmp: Path, on_chunk: Any = None) -> None:
    """Copy ``source`` to ``tmp`` in chunks, calling ``on_chunk(n_bytes)`` after each chunk."""
    with open(source, "rb") as src, open(tmp, "wb") as dst:
        while True:
            buf = src.read(_CHUNK)
            if not buf:
                break
            dst.write(buf)
            if on_chunk is not None:
                on_chunk(len(buf))


def _outside_areas(root: Path, plan: list[dict[str, Any]]) -> list[str]:
    """Return the dests of ``plan`` that lie under no declared area (same rule as ``validate``)."""
    index = AreaIndex(load_project(root).structure)
    return [e["dest"] for e in plan if index.area_of(e["dest"]) is None]


def dry_run_plan(plan_file: str | Path, project: str | Path = ".") -> dict[str, Any]:
    """Run every pre-check of a copy plan, copy nothing and report every problem.

    Each plan entry is counted in every class that applies: ``dest_outside_areas`` (dest under no declared
    area), ``dest_conflict`` (dest exists with other content, or leaves the project), ``source_missing``,
    ``source_changed`` (source size differs from the plan), ``already_match`` (dest already holds the planned
    content) and ``to_copy`` (no problem; a real run would copy it). Digests of sources are not read.

    Returns
    -------
    dict
        ``{ok, dry_run, files, <class>: {files, bytes}, free_bytes, bytes_to_copy, enough_space}``; ``ok``
        is true only when a real run would start (no class other than ``to_copy`` and ``already_match`` is
        non-empty and the free space at the project covers ``bytes_to_copy``). It holds counts and bytes,
        no paths.
    """
    root = find_root(project)
    plan = load_plan(plan_file)
    real_root = root.resolve()
    outside = set(_outside_areas(root, plan))
    names = ("to_copy", "already_match", "dest_conflict", "source_missing", "source_changed", "dest_outside_areas")
    cls: dict[str, list[int]] = {n: [0, 0] for n in names}

    def add(name: str, size: int) -> None:
        cls[name][0] += 1
        cls[name][1] += size

    for e in plan:
        size = e["size"]
        dest = root / e["dest"]
        bad = e["dest"] in outside
        if bad:
            add("dest_outside_areas", size)
        parent = dest.parent.resolve()
        if (parent != real_root and real_root not in parent.parents) or dest.is_symlink():
            add("dest_conflict", size)
            continue
        if dest.exists():
            if dest.is_file() and _verify(dest, size, e["digest"]):
                add("already_match", size)
            else:
                add("dest_conflict", size)
            continue
        src = Path(e["source"])
        if not src.is_file():
            add("source_missing", size)
        elif src.stat().st_size != size:
            add("source_changed", size)
        elif not bad:
            add("to_copy", size)
    to_copy_bytes = cls["to_copy"][1]
    free = shutil.disk_usage(root).free
    enough = free >= to_copy_bytes
    problems = any(cls[n][0] for n in names if n not in ("to_copy", "already_match"))
    out: dict[str, Any] = {"ok": not problems and enough, "dry_run": True, "files": len(plan)}
    out.update({n: {"files": c[0], "bytes": c[1]} for n, c in cls.items()})
    out.update(free_bytes=free, bytes_to_copy=to_copy_bytes, enough_space=enough)
    return out


def adopt_plan(
    plan_file: str | Path,
    project: str | Path = ".",
    *,
    keep_times: bool = True,
    bwlimit: int | None = None,
    progress: Any = None,
    progress_interval: float = 10.0,
) -> dict[str, Any]:
    """Carry out a copy plan inside a project (copy mode).

    Every dest is checked first: one under no declared area (same rule as ``validate``) or one that exists with other content than the plan states refuses the whole
    plan before anything is copied. A dest that already matches is skipped, so a rerun after an interruption
    finishes the rest. Each file is copied to a temporary name, verified for size and sha256, given the
    source's access and modification times (``keep_times``) and then renamed; a mismatch removes the
    temporary file and stops with an error. Sources are only read.

    Parameters
    ----------
    keep_times : bool
        Set the dest's atime and mtime to the source's after verify, before the rename (default on).
    bwlimit : int, optional
        Limit the copy rate to this many bytes per second.
    progress : callable, optional
        Called with ``{files_done, files_total, bytes_done, bytes_total}`` every ``progress_interval``
        seconds and once at the end.

    A SIGINT or SIGTERM during the run removes the current temporary file, stops, and returns the summary
    with ``interrupted`` true and ``signal`` set to the signal name; a rerun resumes.

    Returns
    -------
    dict
        The summary record ``{files, copied, skipped, bytes_total, bytes_copied, interrupted, started,
        finished}``; it is also written to ``.stradler/adopt/summary-<time>.json``. It holds counts, bytes
        and times, no paths.
    """
    root = find_root(project)
    plan = load_plan(plan_file)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    todo: list[tuple[Path, Path, dict[str, Any]]] = []
    skipped = skipped_bytes = 0
    real_root = root.resolve()
    outside = _outside_areas(root, plan)
    if outside:
        raise AdoptError(f"{len(outside)} dest(s) under no declared area, first: {outside[0]}")
    for e in plan:
        dest = root / e["dest"]
        parent = dest.parent.resolve()
        if parent != real_root and real_root not in parent.parents:
            raise AdoptError(f"dest leaves the project: {e['dest']}")
        if dest.is_symlink():
            raise AdoptError(f"dest exists and is not the planned content: {e['dest']}")
        if dest.exists() or dest.is_symlink():
            if not dest.is_file() or not _verify(dest, e["size"], e["digest"]):
                raise AdoptError(f"dest exists with other content: {e['dest']}")
            skipped += 1
            skipped_bytes += e["size"]
            continue
        src = Path(e["source"])
        if not src.is_file():
            raise AdoptError(f"source is not a file: {e['source']}")
        todo.append((src, dest, e))
    bytes_total = sum(e["size"] for e in plan)
    copied = copied_bytes = 0
    stop_sig: list[int] = []
    cur = {"partial": 0}
    last = [_clock()]

    def report(force: bool = False) -> None:
        if progress is None:
            return
        now = _clock()
        if force or now - last[0] >= progress_interval:
            last[0] = now
            progress({"files_done": skipped + copied, "files_total": len(plan),
                      "bytes_done": skipped_bytes + copied_bytes + cur["partial"], "bytes_total": bytes_total})

    pace = {"t0": 0.0, "n": 0}

    def on_chunk(n: int) -> None:
        if stop_sig:
            raise _Stop
        cur["partial"] += n
        if bwlimit:
            pace["n"] += n
            wait = pace["t0"] + pace["n"] / bwlimit - _clock()
            if wait > 0:
                _sleep(wait)
        report()

    old: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for sg in (signal.SIGINT, signal.SIGTERM):
            old[sg] = signal.signal(sg, lambda signum, _f: stop_sig.append(signum))
    try:
        for src, dest, e in todo:
            if stop_sig:
                break
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + TMP_SUFFIX)
            cur["partial"] = 0
            pace["t0"], pace["n"] = _clock(), 0
            st = src.stat()
            try:
                _copy_one(src, tmp, on_chunk)
                if not _verify(tmp, e["size"], e["digest"]):
                    raise AdoptError(f"size or digest mismatch after copy: {e['dest']}")
                if keep_times:
                    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
                os.replace(tmp, dest)
            except _Stop:
                break
            finally:
                if tmp.exists():
                    tmp.unlink()
            cur["partial"] = 0
            copied += 1
            copied_bytes += e["size"]
            report()
    finally:
        for sg, h in old.items():
            signal.signal(sg, h)
    cur["partial"] = 0
    report(force=True)
    summary = {
        "files": len(plan),
        "copied": copied,
        "skipped": skipped,
        "bytes_total": bytes_total,
        "bytes_copied": copied_bytes,
        "interrupted": bool(stop_sig),
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if stop_sig:
        summary["signal"] = signal.Signals(stop_sig[0]).name
    out = root / CONFIG_DIR / "adopt"
    out.mkdir(parents=True, exist_ok=True)
    name = f"summary-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}.json"
    (out / name).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
