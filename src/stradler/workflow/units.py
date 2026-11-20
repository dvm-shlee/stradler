"""Units and levels, and the default data handler (WORKFLOWS.md section 4)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .model import InputsBlock

__all__ = ["DefaultHandler", "Unit", "UnitSet", "UnitsError", "glob_regex"]


class UnitsError(ValueError):
    """Raised when units cannot be discovered."""


def _key(s: str) -> bytes:
    return s.encode("utf-8")


@dataclass(frozen=True)
class Unit:
    """The set of files one task processes at one level.

    Attributes
    ----------
    level : str
        Level name.
    id : str
        Unit identifier, unique within its level; nested identifiers are joined with ``/``.
    files : tuple of str
        Sorted project-relative files the unit covers.
    ancestors : mapping
        Level name to the identifier of the unit's ancestor-or-self at that level.
    """

    level: str
    id: str
    files: tuple[str, ...]
    ancestors: Mapping[str, str] = field(default_factory=dict)


class UnitSet:
    """The units of a dataset, by level.

    Parameters
    ----------
    levels : sequence of str
        Level names, finest first (``file``) to coarsest (``group``).
    units : sequence of Unit
        All units; they are ordered by identifier (UTF-8 bytes) within each level.
    """

    def __init__(self, levels: Sequence[str], units: Sequence[Unit]):
        self.levels = tuple(levels)
        by: dict[str, list[Unit]] = {lvl: [] for lvl in self.levels}
        for u in units:
            by[u.level].append(u)
        self._by = {lvl: tuple(sorted(us, key=lambda u: _key(u.id))) for lvl, us in by.items()}

    def units(self, level: str) -> tuple[Unit, ...]:
        """Return the units at ``level`` in identifier order.

        Parameters
        ----------
        level : str
            Level name.

        Returns
        -------
        tuple of Unit
        """
        return self._by[level]

    def children(self, unit: Unit, level: str) -> tuple[Unit, ...]:
        """Return the units at the finer ``level`` that lie inside ``unit``, in identifier order.

        Parameters
        ----------
        unit : Unit
            Parent unit.
        level : str
            Child level.

        Returns
        -------
        tuple of Unit
        """
        return tuple(u for u in self._by[level] if u.ancestors.get(unit.level) == unit.id)


def glob_regex(pattern: str) -> re.Pattern:
    """Translate a glob pattern into a regular expression.

    Parameters
    ----------
    pattern : str
        ``*`` matches within one path segment, ``**`` across segments, ``?`` one character.

    Returns
    -------
    re.Pattern
        Matches a whole relative POSIX path.
    """
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _patterns(v: object) -> list[str]:
    if isinstance(v, str):
        return [v]
    return list(v)  # type: ignore[arg-type]


class DefaultHandler:
    """The data handler shipped with the core: one ``file`` unit per file, one ``group`` unit.

    The handler has no intermediate levels. It reads the ``filter`` keys ``include`` and ``exclude`` (glob
    patterns relative to the dataset folder) and refuses any other key and any ``units`` selection.
    """

    levels = ("file", "group")

    def discover(self, root: str | os.PathLike, inputs: InputsBlock) -> UnitSet:
        """Discover the units of a dataset.

        Parameters
        ----------
        root : str or os.PathLike
            Project root.
        inputs : InputsBlock
            The recipe's ``inputs`` block.

        Returns
        -------
        UnitSet
            File units whose identifier is the project-relative path, and the group unit ``group``.

        Raises
        ------
        UnitsError
            If the dataset folder is missing, or the filter or ``units`` selection is not supported.
        """
        root = os.fspath(root)
        extra = [k for k in inputs.filter if k not in ("include", "exclude")]
        if extra:
            raise UnitsError(f"default handler does not read filter keys: {', '.join(sorted(extra))}")
        if inputs.units:
            raise UnitsError("default handler has no intermediate levels; inputs.units is not supported")
        base = os.path.join(root, inputs.dataset)
        if not os.path.isdir(base):
            raise UnitsError(f"dataset folder does not exist: {inputs.dataset}")
        include = [glob_regex(p) for p in _patterns(inputs.filter["include"])] if "include" in inputs.filter else None
        exclude = [glob_regex(p) for p in _patterns(inputs.filter.get("exclude", []))]
        files: list[str] = []
        for dirpath, _dirs, names in os.walk(base, followlinks=False):
            for n in names:
                full = os.path.join(dirpath, n)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                if include is not None and not any(p.match(rel) for p in include):
                    continue
                if any(p.match(rel) for p in exclude):
                    continue
                files.append(inputs.dataset.rstrip("/") + "/" + rel)
        files.sort(key=_key)
        units = [Unit("file", f, (f,), {"file": f, "group": "group"}) for f in files]
        units.append(Unit("group", "group", tuple(files), {"group": "group"}))
        return UnitSet(self.levels, units)
