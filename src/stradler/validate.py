"""Project validation: schemas first, then cross-file, mutability and agent rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import schema as sch
from .project import Project, load_project
from .workflow.areas import CORE_ROLES, GRADES, grade_rank

__all__ = ["resolve_schema", "validate_path", "validate_project"]

_STORE_ADDON_KINDS = ("store", "remote")
_MUTABILITIES = ("immutable", "sealed", "mutable")
_AGENT_KEYS = ("search", "read", "write")


def _role_problems(roles: dict) -> list[str]:
    """Rules for the user-defined roles of structure 1.1.0 that the JSON Schema cannot express."""
    out = []
    for name, r in roles.items():
        where = f"structure.toml: roles.{name}"
        if name in CORE_ROLES:
            out.append(f"{where}: name equals a core role")
        base = r.get("base")
        if base in roles:
            out.append(f"{where}.base: {base!r} is a user role; the base must be a core role (one level only)")
        elif base not in CORE_ROLES:
            out.append(f"{where}.base: {base!r} is not a core role")
        mut = r.get("mutability")
        if mut is not None and not mut:
            out.append(f"{where}.mutability: must not be empty")
        if r.get("agent", {}).get("write") is True and mut is not None and "mutable" not in mut:
            out.append(f"{where}: agent write = true although no allowed mutability is mutable "
                       "(a role may only narrow its base, never loosen it)")
    return out


def _replica_problems(cfg: dict, structure: dict) -> list[str]:
    """Rules for ``[replicas.<area>]`` of config 1.1.0 that the JSON Schema cannot express."""
    out = []
    stores = cfg.get("stores", {})
    areas = structure.get("areas", {})
    for name, r in cfg.get("replicas", {}).items():
        where = f"config.toml: replicas.{name}"
        if name not in areas:
            out.append(f"{where}: no such area")
        own = areas.get(name, {}).get("store", areas.get(name, {}).get("source"))
        if own is None:
            own = cfg.get("true_sources", {}).get(name, {}).get("store")
        for s in r["stores"]:
            if s not in stores:
                out.append(f"{where}.stores: unknown store {s!r}")
            elif s == own:
                out.append(f"{where}.stores: {s!r} is the area's own store")
        mc = r.get("min_copies")
        if mc is not None and mc > len(r["stores"]):
            out.append(f"{where}.min_copies: {mc} is more than the {len(r['stores'])} declared store(s)")
    return out


def resolve_schema(project: Project, ref: str) -> dict:
    """Resolve a schema reference to a schema, offline.

    Looks in the project's ``.stradler/schema/`` first, then in the bundled
    core schemas. A ``sha256:`` reference is matched by file digest.

    Parameters
    ----------
    project : Project
        The project whose schema copies are searched.
    ref : str
        ``<name>@<version>`` or ``sha256:<hex>``.

    Raises
    ------
    stradler.schema.SchemaError
        If the reference cannot be resolved.
    """
    dirs = [project.config_dir / "schema", sch.bundled_schema_dir()]
    if ref.startswith("sha256:"):
        for d in dirs:
            for f in sorted(d.glob("*.json")) if d.is_dir() else []:
                if sch.schema_digest(f) == ref:
                    return json.loads(f.read_text(encoding="utf-8"))
        raise sch.SchemaError(f"no schema file with digest {ref}")
    m = re.fullmatch(r"([A-Za-z0-9_-]+)@([0-9]+\.[0-9]+\.[0-9]+)", ref)
    if not m:
        raise sch.SchemaError(f"bad schema reference {ref!r}")
    for d in dirs:
        f = d / f"{ref}.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    raise sch.SchemaError(f"schema {ref} not found")


def _shape(project: Project, label: str, doc: dict | None, ref: str, out: list[str]) -> bool:
    if doc is None:
        return False
    try:
        schema = resolve_schema(project, ref)
    except sch.SchemaError as e:
        out.append(f"{label}: {e}")
        return False
    errs = sch.validate(doc, schema)
    out.extend(f"{label}: {e}" for e in errs)
    return not errs


def validate_project(project: Project) -> list[str]:
    """Validate a loaded project.

    Checks shape against the JSON Schemas, then the rules the schemas cannot
    express: ``write`` access never exceeds mutability, store, source and
    handler names resolve, ``deletable_after`` names areas, paths are unique,
    and ``template.json`` and ``local.toml`` agree with ``config.toml``.

    Parameters
    ----------
    project : Project
        Result of :func:`stradler.project.load_project`.

    Returns
    -------
    list of str
        One message per problem; empty when the project is valid.
    """
    out = list(project.read_errors)
    marker_ok = _shape(project, "stradler.json", project.marker, "stradler@1.0.0", out)
    cfg_ref = (project.config or {}).get("schema")
    str_ref = (project.structure or {}).get("schema")
    cfg_ok = _shape(project, "config.toml", project.config, cfg_ref, out) if isinstance(cfg_ref, str) else False
    str_ok = _shape(project, "structure.toml", project.structure, str_ref, out) if isinstance(str_ref, str) else False
    if project.config is not None and not isinstance(cfg_ref, str):
        out.append("config.toml: missing or non-string 'schema'")
    if project.structure is not None and not isinstance(str_ref, str):
        out.append("structure.toml: missing or non-string 'schema'")
    if project.template is not None:
        t = project.template
        if not (isinstance(t, dict) and set(t) == {"name", "version", "digest"}
                and all(isinstance(v, str) for v in t.values())
                and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", t["version"])
                and re.fullmatch(r"sha256:[0-9a-f]{64}", t["digest"]) and t["name"]):
            out.append("template.json: needs exactly name, version (MAJOR.MINOR.PATCH) and digest (sha256:<hex>)")
    if marker_ok and project.marker and project.marker.get("config_dir") != ".stradler":
        out.append("stradler.json: config_dir mismatch")

    cfg = project.config if cfg_ok else {}
    addons = cfg.get("addons", {}) if cfg else {}
    stores = cfg.get("stores", {}) if cfg else {}
    if cfg_ok:
        for name, s in stores.items():
            a = addons.get(s["addon"])
            if a is None:
                out.append(f"config.toml: store {name!r} names unknown add-on {s['addon']!r}")
            elif a["kind"] not in _STORE_ADDON_KINDS:
                out.append(f"config.toml: store {name!r}: add-on {s['addon']!r} has kind {a['kind']!r}, need store or remote")
    areas = project.structure.get("areas", {}) if str_ok else {}
    if cfg_ok and str_ok:
        for name, ts in cfg.get("true_sources", {}).items():
            if name not in areas:
                out.append(f"config.toml: true_sources.{name}: no such area")
            if ts["store"] not in stores:
                out.append(f"config.toml: true_sources.{name}: unknown store {ts['store']!r}")
            elif "source" in areas.get(name, {}) and areas[name]["source"] != ts["store"]:
                out.append(f"structure.toml: areas.{name}.source disagrees with true_sources.{name}.store")
    if cfg_ok and str_ok:
        out.extend(_replica_problems(cfg, project.structure))
    if str_ok:
        paths: dict[str, str] = {}
        roles = project.structure.get("roles", {})
        out.extend(_role_problems(roles))
        for name, a in areas.items():
            where = f"structure.toml: areas.{name}"
            mut = a["mutability"]
            if a.get("agent", {}).get("write") is True and mut != "mutable":
                out.append(f"{where}: agent write = true on a {mut} area (write never exceeds mutability)")
            role = a["role"]
            urole = roles.get(role) if role not in CORE_ROLES else None
            if role not in CORE_ROLES and urole is None:
                out.append(f"{where}.role: {role!r} is neither a core role nor defined in roles")
            if urole is not None:
                allowed = urole.get("mutability")
                if allowed is not None and mut not in allowed:
                    out.append(f"{where}.mutability: {mut!r} is not allowed by role {role!r} "
                               f"(allowed: {', '.join(allowed)})")
                bound = urole.get("agent")
                if bound is not None:
                    for k in _AGENT_KEYS:
                        if a.get("agent", {}).get(k) is True and bound.get(k) is not True:
                            out.append(f"{where}.agent.{k}: role {role!r} does not allow it")
                if "min_grade" in urole and grade_rank(a.get("grade")) < GRADES.index(urole["min_grade"]):
                    out.append(f"{where}.grade: {a.get('grade', 'private')!r} is less strict than min_grade "
                               f"{urole['min_grade']!r} of role {role!r}")
                role = urole.get("base")
            if role == "agent" and "path" not in a:
                out.append(f"{where}: role agent needs a path")
            for key in ("store", "source"):
                if key in a and cfg_ok and a[key] not in stores:
                    out.append(f"{where}.{key}: unknown store {a[key]!r}")
            if "handler" in a and cfg_ok:
                ad = addons.get(a["handler"])
                if ad is None or ad["kind"] != "data_handler":
                    out.append(f"{where}.handler: {a['handler']!r} is not a configured add-on of kind data_handler")
            for d in a.get("deletable_after", []):
                if d not in areas:
                    out.append(f"{where}.deletable_after: unknown area {d!r}")
                elif d == name:
                    out.append(f"{where}.deletable_after: area lists itself")
            if "path" in a:
                p = a["path"].rstrip("/")
                if p in paths:
                    out.append(f"{where}.path: same as areas.{paths[p]}")
                paths[p] = name
    if cfg_ok and project.local is not None:
        loc = project.local
        for sect, known in (("stores", stores), ("addons", addons)):
            for name in loc.get(sect, {}):
                if name not in known:
                    out.append(f"local.toml: {sect}.{name} is not defined in config.toml")
        for k in loc:
            if k not in ("stores", "addons"):
                out.append(f"local.toml: unknown table {k!r}")
    return out


def validate_path(start: str | Path = ".") -> tuple[Path, list[str]]:
    """Find the project at or above ``start`` and validate it.

    Returns
    -------
    tuple
        ``(root, problems)``.

    Raises
    ------
    stradler.project.ProjectError
        If there is no project.
    """
    prj = load_project(start)
    return prj.root, validate_project(prj)
