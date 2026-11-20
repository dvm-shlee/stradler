"""Canonical JSON, digests, input digests and task identity (WORKFLOWS.md sections 7.1, 8.2, 10)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

__all__ = [
    "bound_paths",
    "canonical_json",
    "check_unit_id",
    "combine_components",
    "digest_of",
    "input_components",
    "input_digest",
    "output_stats",
    "sha256_digest",
    "task_record_dir",
]

_LIMIT = 2**53


def canonical_json(value: Any) -> str:
    """Serialize ``value`` as canonical JSON (section 10.1).

    Parameters
    ----------
    value : object
        ``dict`` with string keys, ``list``, ``str``, ``int``, ``bool`` or ``None``.

    Returns
    -------
    str
        UTF-8 text with sorted keys, no whitespace and only integer numbers.

    Raises
    ------
    ValueError
        For floats, integers beyond 2**53 in magnitude, or other types.
    """
    out: list[str] = []
    _emit(value, out)
    return "".join(out)


def _emit(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > _LIMIT:
            raise ValueError(f"integer out of canonical range: {value}")
        out.append(str(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, out)
        out.append("]")
    elif isinstance(value, Mapping):
        bad = [k for k in value if not isinstance(k, str)]
        if bad:
            raise ValueError(f"object key is not a string: {bad[0]!r}")
        out.append("{")
        for i, k in enumerate(sorted(value, key=lambda s: s.encode("utf-8"))):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _emit(value[k], out)
        out.append("}")
    else:
        raise ValueError(f"value not representable in canonical JSON: {type(value).__name__}")


def sha256_digest(data: bytes) -> str:
    """Return the digest text ``sha256:<hex>`` of ``data`` (section 10.2).

    Parameters
    ----------
    data : bytes
        Bytes to hash.

    Returns
    -------
    str
        ``sha256:`` followed by 64 lowercase hexadecimal digits.
    """
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_of(value: Any) -> str:
    """Return the digest of the canonical JSON of ``value``.

    Parameters
    ----------
    value : object
        Any value accepted by :func:`canonical_json`.

    Returns
    -------
    str
        Digest in the form of section 10.2.
    """
    return sha256_digest(canonical_json(value).encode("utf-8"))


def bound_paths(bound: Mapping[str, Any]) -> list[str]:
    """Collect the distinct paths of bound values, sorted as UTF-8 bytes.

    Parameters
    ----------
    bound : mapping
        Placeholder name to a path or a list of paths.

    Returns
    -------
    list of str
        Unique paths in byte order.
    """
    paths: set[str] = set()
    for v in bound.values():
        if isinstance(v, str):
            paths.add(v)
        else:
            paths.update(v)
    return sorted(paths, key=lambda s: s.encode("utf-8"))


def _regular_files(root: str, rel: str) -> list[str]:
    full = os.path.join(root, rel)
    if os.path.isfile(full):
        return [rel]
    found: list[str] = []
    if os.path.isdir(full):
        for dirpath, _dirs, files in os.walk(full, followlinks=False):
            for f in files:
                p = os.path.join(dirpath, f)
                if os.path.isfile(p):
                    found.append(os.path.relpath(p, root).replace(os.sep, "/"))
    return sorted(found, key=lambda s: s.encode("utf-8"))


def input_digest(root: str | os.PathLike, bound: Mapping[str, Any]) -> str:
    """Digest the content of the bound input values (section 10.3).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    bound : mapping
        Placeholder name to a project-relative path or list of paths.

    Returns
    -------
    str
        Digest of the paths and the length-prefixed content of the files under them.
    """
    root = os.fspath(root)
    h = hashlib.sha256()
    for p in bound_paths(bound):
        h.update(p.encode("utf-8") + b"\x00")
        files = _regular_files(root, p)
        if not files:
            h.update(b"missing\x00")
            continue
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                data = fh.read()
            h.update(f.encode("utf-8") + b"\x00" + len(data).to_bytes(8, "big") + data)
    return "sha256:" + h.hexdigest()


def output_stats(root: str | os.PathLike, path: str) -> dict[str, int] | None:
    """Measure an output (section 9.3).

    Parameters
    ----------
    root : str or os.PathLike
        Project root.
    path : str
        Project-relative output path.

    Returns
    -------
    dict or None
        ``{"size", "mtime_ns"}``; a directory has the summed size of its files and the newest mtime of
        itself and its contents. ``None`` when the path does not exist.
    """
    full = os.path.join(os.fspath(root), path)
    if not os.path.exists(full):
        return None
    st = os.stat(full)
    if not os.path.isdir(full):
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    size, newest = 0, st.st_mtime_ns
    for dirpath, dirs, files in os.walk(full):
        for name in dirs + files:
            s = os.stat(os.path.join(dirpath, name))
            newest = max(newest, s.st_mtime_ns)
            if name in files:
                size += s.st_size
    return {"size": size, "mtime_ns": newest}


def input_components(inputs: str, argv: list[str], pin_sha256: str | None, workflow: tuple[str, str, str]) -> dict:
    """Build the four components of a task's input digest (section 8.2).

    Parameters
    ----------
    inputs : str
        Input digest of the bound values (:func:`input_digest`).
    argv : list of str
        Planned argument vector.
    pin_sha256 : str or None
        ``pin.sha256`` as written (lowercase) for a container; ``None`` for no container.
    workflow : tuple of str
        ``(name, family, version)`` of the recipe.

    Returns
    -------
    dict
        Keys ``inputs``, ``command``, ``container`` and ``workflow``, each a digest.
    """
    return {
        "inputs": inputs,
        "command": digest_of(list(argv)),
        "container": sha256_digest((pin_sha256 or "").encode("utf-8")),
        "workflow": digest_of(list(workflow)),
    }


def combine_components(components: Mapping[str, str]) -> str:
    """Return ``input_digest`` of a task: the digest of its component map (section 8.2).

    Parameters
    ----------
    components : mapping
        The four component digests.

    Returns
    -------
    str
        Digest of the canonical JSON of the map.
    """
    return digest_of(dict(components))


def check_unit_id(unit: str) -> None:
    """Refuse a unit identifier that cannot name a record directory (section 7.1).

    Parameters
    ----------
    unit : str
        Unit identifier.

    Raises
    ------
    ValueError
        If the identifier is empty, absolute, or has an empty, ``.`` or ``..`` segment.
    """
    segs = unit.split("/")
    if not unit or unit.startswith("/") or any(s in ("", ".", "..") for s in segs):
        raise ValueError(f"unit identifier cannot name a record directory: {unit!r}")


def task_record_dir(run_dir: str, step: str, unit: str) -> str:
    """Return ``<run_dir>/<step>/<unit>`` for a task (section 7.1).

    Parameters
    ----------
    run_dir : str
        Run directory.
    step : str
        Step name.
    unit : str
        Unit identifier; ``/`` gives nested directories.

    Returns
    -------
    str
        The task's record directory.
    """
    check_unit_id(unit)
    return "/".join([run_dir.rstrip("/"), step, unit])
