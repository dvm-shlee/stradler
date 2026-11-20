"""``stradler init``: write ``stradler.json`` and ``.stradler/`` from a template."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

from . import schema as sch
from . import tomlw, yamlsubset
from .project import CONFIG_DIR, MARKER

__all__ = ["SPEC_VERSION", "InitError", "init_project", "load_template"]

SPEC_VERSION = "1.0.0"


class InitError(Exception):
    """Raised when a project cannot be initialised."""


def load_template(path: str | Path) -> tuple[dict, str]:
    """Read a project template file.

    The format follows the extension: ``.yaml``/``.yml`` (strict subset),
    ``.toml`` or ``.json``. A template has ``name`` and ``version``, an
    ``areas`` table (as in ``structure.toml``) and an optional ``config``
    table (as in ``config.toml``, without ``schema``).

    Returns
    -------
    tuple
        ``(template dict, digest)`` where digest is ``sha256:<hex>`` of the file bytes.
    """
    p = Path(path)
    if not p.is_file():
        raise InitError(f"template not found: {p}")
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    try:
        if p.suffix in (".yaml", ".yml"):
            doc = yamlsubset.loads(text)
        elif p.suffix == ".toml":
            doc = tomllib.loads(text)
        elif p.suffix == ".json":
            doc = json.loads(text)
        else:
            raise InitError(f"unsupported template extension {p.suffix!r} (use .yaml, .yml, .toml or .json)")
    except (ValueError, yamlsubset.YamlSubsetError) as e:
        raise InitError(f"template {p.name}: {e}") from e
    if not isinstance(doc, dict):
        raise InitError("template must be a mapping")
    extra = set(doc) - {"name", "version", "project_id", "areas", "config"}
    if extra:
        raise InitError(f"template: unknown keys {sorted(extra)}")
    for k in ("name", "version", "areas"):
        if k not in doc:
            raise InitError(f"template: missing {k!r}")
    if not isinstance(doc["name"], str) or not doc["name"]:
        raise InitError("template: name must be a non-empty string")
    if not isinstance(doc["version"], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", doc["version"]):
        raise InitError("template: version must be MAJOR.MINOR.PATCH")
    if p.suffix in (".yaml", ".yml"):
        doc["areas"] = sch.coerce(doc["areas"], sch.load_bundled("structure")["properties"]["areas"])
    return doc, "sha256:" + hashlib.sha256(raw).hexdigest()


def init_project(template: str | Path, target: str | Path = ".", project_id: str | None = None) -> Path:
    """Create a project in ``target`` from a template.

    Writes ``stradler.json``, ``.stradler/config.toml``, ``structure.toml``,
    ``template.json``, ``local.toml``, a ``.gitignore`` that excludes
    ``local.toml``, copies of the three core schemas in ``.stradler/schema/``,
    and the declared area folders. The result is validated; on failure
    nothing is left behind.

    Parameters
    ----------
    template : str or pathlib.Path
        Template file.
    target : str or pathlib.Path
        Directory to initialise (created if needed).
    project_id : str, optional
        Project identifier; defaults to the template's ``project_id`` or the
        directory name.

    Returns
    -------
    pathlib.Path
        The project root.

    Raises
    ------
    InitError
        If the target already is a project, the template is invalid, or the
        resulting project does not validate.
    """
    doc, digest = load_template(template)
    root = Path(target).resolve()
    if (root / MARKER).exists() or (root / CONFIG_DIR).exists():
        raise InitError(f"{root} already holds a stradler project")
    pid = project_id or doc.get("project_id") or root.name
    if not isinstance(pid, str) or not pid:
        raise InitError("project id must be a non-empty string")

    _materialize(root, doc, digest, pid, make_areas=True, write_template=True)
    return root


def _materialize(root: Path, doc: dict, digest: str, pid: str, *, make_areas: bool, write_template: bool,
                 keep_root: bool = False, structure_schema: str = "structure@1.0.0") -> None:
    """Write the project files for ``doc`` into ``root`` and validate; remove them again on failure."""
    from .project import load_project
    from .validate import validate_project

    cfg = dict(doc.get("config", {}))
    if not isinstance(cfg, dict) or "schema" in cfg:
        raise InitError("template: config must be a table without 'schema'")
    if isinstance(doc.get("config"), dict):
        cfg = sch.coerce(cfg, sch.load_bundled("config"))
    config = {"schema": "config@1.0.0", **cfg}
    structure = {"schema": structure_schema, "areas": doc["areas"]}
    marker = {"marker": "stradler-project", "spec_version": SPEC_VERSION, "project_id": pid, "config_dir": CONFIG_DIR}

    # Validate in a scratch tree first, so a bad template leaves nothing behind.
    created_root = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        cd = root / CONFIG_DIR
        (cd / "schema").mkdir(parents=True)
        written.append(cd)
        (root / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        written.append(root / MARKER)
        (cd / "config.toml").write_text(tomlw.dumps(config), encoding="utf-8")
        (cd / "structure.toml").write_text(tomlw.dumps(structure), encoding="utf-8")
        tpl = {"name": doc.get("name"), "version": doc.get("version"), "digest": digest}
        if write_template:
            (cd / "template.json").write_text(json.dumps(tpl, indent=2) + "\n", encoding="utf-8")
        (cd / "local.toml").write_text("# Machine-local settings (real paths, endpoints). Not tracked in git.\n", encoding="utf-8")
        (cd / ".gitignore").write_text("local.toml\n", encoding="utf-8")
        for name in sch.CORE_SCHEMAS:
            src = sch.bundled_schema_dir() / f"{name}@{SPEC_VERSION}.json"
            shutil.copyfile(src, cd / "schema" / src.name)
        if structure_schema != "structure@1.0.0":
            shutil.copyfile(sch.bundled_schema_dir() / f"{structure_schema}.json", cd / "schema" / f"{structure_schema}.json")
        problems = validate_project(load_project(root))
        if problems:
            raise InitError("template gives an invalid project:\n  " + "\n  ".join(problems))
        for a in doc["areas"].values():
            if make_areas and isinstance(a, dict) and "path" in a:
                (root / a["path"]).mkdir(parents=True, exist_ok=True)
    except BaseException:
        for p in written:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        if created_root and not keep_root:
            shutil.rmtree(root, ignore_errors=True)
        raise
