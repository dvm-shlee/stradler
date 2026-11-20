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
import time
from pathlib import Path
from typing import Any

from . import schema as sch
from .init import SPEC_VERSION, InitError, _materialize, load_template
from .project import CONFIG_DIR, MARKER, find_root
from .snapshot import SnapshotError, file_digest

__all__ = ["AdoptError", "adopt_in_place", "adopt_plan", "load_plan", "propose"]

_NAME = re.compile(r"[^A-Za-z0-9_-]")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TMP_SUFFIX = ".stradler-part"


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


def _copy_one(source: Path, tmp: Path) -> None:
    shutil.copyfile(source, tmp)


def adopt_plan(plan_file: str | Path, project: str | Path = ".") -> dict[str, Any]:
    """Carry out a copy plan inside a project (copy mode).

    Every dest is checked first: one that exists with other content than the plan states refuses the whole
    plan before anything is copied. A dest that already matches is skipped, so a rerun after an interruption
    finishes the rest. Each file is copied to a temporary name, verified for size and sha256 and then renamed;
    a mismatch removes the temporary file and stops with an error. Sources are only read.

    Returns
    -------
    dict
        The summary record ``{files, copied, skipped, bytes_total, bytes_copied, started, finished}``; it is
        also written to ``.stradler/adopt/summary-<time>.json``. It holds counts, bytes and times, no paths.
    """
    root = find_root(project)
    plan = load_plan(plan_file)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    todo: list[tuple[Path, Path, dict[str, Any]]] = []
    skipped = 0
    real_root = root.resolve()
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
            continue
        src = Path(e["source"])
        if not src.is_file():
            raise AdoptError(f"source is not a file: {e['source']}")
        todo.append((src, dest, e))
    copied = copied_bytes = 0
    for src, dest, e in todo:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + TMP_SUFFIX)
        try:
            _copy_one(src, tmp)
            if not _verify(tmp, e["size"], e["digest"]):
                raise AdoptError(f"size or digest mismatch after copy: {e['dest']}")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        copied += 1
        copied_bytes += e["size"]
    summary = {
        "files": len(plan),
        "copied": copied,
        "skipped": skipped,
        "bytes_total": sum(e["size"] for e in plan),
        "bytes_copied": copied_bytes,
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = root / CONFIG_DIR / "adopt"
    out.mkdir(parents=True, exist_ok=True)
    name = f"summary-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}.json"
    (out / name).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
