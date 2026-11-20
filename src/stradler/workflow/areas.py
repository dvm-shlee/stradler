"""Lookup of project areas by path."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["CORE_ROLES", "DEFAULT_GRADE", "GRADES", "AreaIndex", "grade_rank"]

CORE_ROLES = ("input", "output", "code", "recipe", "image", "record", "agent")
GRADES = ("public", "work", "confidential", "private")
DEFAULT_GRADE = "private"


def grade_rank(grade: str | None) -> int:
    """Return the strictness rank of a grade (0 public .. 3 private).

    An absent or unknown grade ranks as ``private`` (fail-closed).
    """
    return GRADES.index(grade) if grade in GRADES else len(GRADES) - 1


class AreaIndex:
    """Index of the areas of a project structure.

    Parameters
    ----------
    structure : mapping or None
        Parsed ``structure.toml``; only areas with a ``path`` are indexed. An area of a user-defined role
        (``[roles.<name>]``, structure 1.1.0) is indexed as an area of the base role of that role: its ``role`` is
        the base role and its declared role is kept under ``role_name``.
    """

    def __init__(self, structure: Mapping[str, Any] | None):
        areas = (structure or {}).get("areas", {})
        roles = (structure or {}).get("roles", {})
        self.areas: dict[str, dict] = {}
        for n, a in areas.items():
            if not (isinstance(a, dict) and isinstance(a.get("path"), str)):
                continue
            r = roles.get(a.get("role")) if isinstance(roles, dict) else None
            if isinstance(r, dict) and a.get("role") not in CORE_ROLES:
                a = {**a, "role": r.get("base"), "role_name": a["role"]}
            self.areas[n] = a

    def area_of(self, relpath: str) -> tuple[str, dict] | None:
        """Return the innermost area that holds ``relpath``.

        Parameters
        ----------
        relpath : str
            Project-relative POSIX path.

        Returns
        -------
        tuple or None
            ``(name, area)`` or ``None``.
        """
        p = relpath.strip("/")
        best = None
        for name, area in self.areas.items():
            ap = area["path"].strip("/")
            if (p == ap or p.startswith(ap + "/")) and (
                    best is None or len(ap) > len(best[1]["path"].strip("/"))):
                best = (name, area)
        return best

    def has_role(self, relpath: str, roles: tuple[str, ...]) -> bool:
        """Tell whether ``relpath`` lies in an area whose role is in ``roles``.

        Parameters
        ----------
        relpath : str
            Project-relative path.
        roles : tuple of str
            Accepted roles.

        Returns
        -------
        bool
        """
        hit = self.area_of(relpath)
        return hit is not None and hit[1].get("role") in roles

    def mutability_of(self, relpath: str) -> str | None:
        """Return the mutability of the area holding ``relpath``, or ``None``.

        Parameters
        ----------
        relpath : str
            Project-relative path.

        Returns
        -------
        str or None
        """
        hit = self.area_of(relpath)
        return hit[1].get("mutability") if hit else None

    def record_path(self) -> str | None:
        """Return the path of the first area with role ``record``, or ``None``.

        Returns
        -------
        str or None
        """
        for area in self.areas.values():
            if area.get("role") == "record":
                return area["path"].strip("/")
        return None

    def grade_of(self, relpath: str) -> str | None:
        """Return the grade of the area holding ``relpath``.

        Parameters
        ----------
        relpath : str
            Project-relative path.

        Returns
        -------
        str or None
            The area's ``grade``, ``private`` when the area declares none, ``None`` when no area holds the path.
        """
        hit = self.area_of(relpath)
        if hit is None:
            return None
        g = hit[1].get("grade")
        return g if g in GRADES else DEFAULT_GRADE
