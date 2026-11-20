"""Filesystem hook: how the planner and the executor ask whether a project path exists (WORKFLOWS.md 5.6)."""

from __future__ import annotations

import os
from typing import Protocol

from .identity import output_stats


class FS(Protocol):
    """Existence and listing queries over project-relative paths."""

    def exists(self, rel: str) -> bool: ...
    def is_file(self, rel: str) -> bool: ...
    def is_dir(self, rel: str) -> bool: ...
    def list_dir(self, rel: str) -> list[str]: ...
    def lexists(self, rel: str) -> bool: ...
    def is_link(self, rel: str) -> bool: ...
    def stat(self, rel: str) -> dict[str, int] | None: ...
    def read_text(self, rel: str) -> str: ...


class LocalFS:
    """Default ``fs``: plain files below ``root`` on the local disk."""

    def __init__(self, root: str | os.PathLike):
        self.root = os.fspath(root)

    def exists(self, rel: str) -> bool:
        return os.path.exists(os.path.join(self.root, rel))

    def is_file(self, rel: str) -> bool:
        return os.path.isfile(os.path.join(self.root, rel))

    def is_dir(self, rel: str) -> bool:
        return os.path.isdir(os.path.join(self.root, rel))

    def list_dir(self, rel: str) -> list[str]:
        return os.listdir(os.path.join(self.root, rel))

    def lexists(self, rel: str) -> bool:
        return os.path.lexists(os.path.join(self.root, rel))

    def is_link(self, rel: str) -> bool:
        return os.path.islink(os.path.join(self.root, rel))

    def stat(self, rel: str) -> dict[str, int] | None:
        return output_stats(self.root, rel)

    def read_text(self, rel: str) -> str:
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()
