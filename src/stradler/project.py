"""Project discovery and loading."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CONFIG_DIR", "MARKER", "Project", "ProjectError", "find_root", "load_project"]

MARKER = "stradler.json"
CONFIG_DIR = ".stradler"


class ProjectError(Exception):
    """Raised when a project cannot be found or one of its files cannot be read."""


def find_root(start: str | Path = ".") -> Path:
    """Walk up from ``start`` to the nearest directory holding ``stradler.json``.

    Parameters
    ----------
    start : str or pathlib.Path
        Directory (or file) to start from.

    Returns
    -------
    pathlib.Path
        The project root.

    Raises
    ------
    ProjectError
        If no ``stradler.json`` exists in ``start`` or any parent.
    """
    p = Path(start).resolve()
    if p.is_file():
        p = p.parent
    for d in (p, *p.parents):
        if (d / MARKER).is_file():
            return d
    raise ProjectError(f"no {MARKER} in {p} or any parent directory")


@dataclass
class Project:
    """A loaded project: parsed files and the problems met while reading them.

    Attributes
    ----------
    root : pathlib.Path
        Project root.
    marker, config, structure, template, local : dict or None
        Parsed ``stradler.json``, ``config.toml``, ``structure.toml``,
        ``template.json`` and ``local.toml``; ``None`` when absent or unreadable.
    read_errors : list of str
        Messages for files that are missing (required ones) or do not parse.
    """

    root: Path
    marker: dict | None = None
    config: dict | None = None
    structure: dict | None = None
    template: dict | None = None
    local: dict | None = None
    read_errors: list[str] = field(default_factory=list)

    @property
    def config_dir(self) -> Path:
        """Path of the ``.stradler`` directory."""
        return self.root / CONFIG_DIR


def _read(path: Path, kind: str, required: bool, errors: list[str]) -> Any:
    if not path.is_file():
        if required:
            errors.append(f"{path.name}: file is missing")
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if kind == "json" else tomllib.loads(text)
    except (ValueError, OSError) as e:  # JSONDecodeError and TOMLDecodeError are ValueErrors
        errors.append(f"{path.name}: cannot parse: {e}")
        return None


def load_project(start: str | Path = ".") -> Project:
    """Discover the project at or above ``start`` and parse its files.

    Parameters
    ----------
    start : str or pathlib.Path
        Directory to start the search from.

    Returns
    -------
    Project
        Parsed content; parse problems are collected in ``read_errors``.

    Raises
    ------
    ProjectError
        If no project root is found.
    """
    root = find_root(start)
    prj = Project(root=root)
    cd = prj.config_dir
    prj.marker = _read(root / MARKER, "json", True, prj.read_errors)
    prj.config = _read(cd / "config.toml", "toml", True, prj.read_errors)
    prj.structure = _read(cd / "structure.toml", "toml", True, prj.read_errors)
    prj.template = _read(cd / "template.json", "json", False, prj.read_errors)
    prj.local = _read(cd / "local.toml", "toml", False, prj.read_errors)
    return prj
