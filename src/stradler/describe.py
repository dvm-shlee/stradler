"""``stradler describe``: a public, versioned description of a project's structure."""

from __future__ import annotations

from typing import Any

from .project import Project
from .replicas import replica_status
from .workflow.areas import CORE_ROLES, DEFAULT_GRADE

__all__ = ["DESCRIBE_VERSION", "agent_view", "describe_project", "render_agent_text", "render_text"]

DESCRIBE_VERSION = 2
_MUTABILITIES = ["immutable", "sealed", "mutable"]
_AGENT_KEYS = ("search", "read", "write")


def _agent(a: Any) -> dict[str, bool]:
    a = a if isinstance(a, dict) else {}
    return {k: a.get(k) is True for k in _AGENT_KEYS}


def _table(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def describe_project(project: Project) -> dict[str, Any]:
    """Build the describe document of a loaded project.

    Only content of ``stradler.json``, ``config.toml`` and ``structure.toml`` is used; ``local.toml`` is never read
    into the result. The key order is canonical and the entries of every list are sorted by name.

    Parameters
    ----------
    project : Project
        A loaded project.

    Returns
    -------
    dict
        The document (``describe_version`` 1), ready for ``json.dumps`` without key sorting.
    """
    marker, config, structure = project.marker or {}, project.config or {}, project.structure or {}
    roles = _table(structure, "roles")
    true_sources = _table(config, "true_sources")
    areas = []
    for name in sorted(_table(structure, "areas")):
        a = structure["areas"][name]
        declared = a.get("role")
        base = roles[declared].get("base") if declared not in CORE_ROLES and declared in roles else declared
        store = a.get("store", a.get("source"))
        if store is None and isinstance(true_sources.get(name), dict):
            store = true_sources[name].get("store")
        areas.append({
            "name": name,
            "path": a.get("path"),
            "role": declared,
            "base_role": base,
            "mutability": a.get("mutability"),
            "grade": a.get("grade", DEFAULT_GRADE),
            "store": store,
            "manifest": a.get("manifest"),
            "agent": _agent(a.get("agent")),
            "deletable_after": sorted(a.get("deletable_after", [])),
        })
    user_roles = []
    for name in sorted(roles):
        r = roles[name]
        user_roles.append({
            "name": name,
            "base": r.get("base"),
            "description": r.get("description"),
            "mutability": list(r.get("mutability", _MUTABILITIES)),
            "agent": _agent(r["agent"]) if isinstance(r.get("agent"), dict) else None,
            "min_grade": r.get("min_grade"),
        })
    addons, pins, stores = _table(config, "addons"), _table(config, "pins"), _table(config, "stores")
    return {
        "describe_version": DESCRIBE_VERSION,
        "project": {
            "name": marker.get("project_id"),
            "spec_version": marker.get("spec_version"),
            "config_schema": config.get("schema"),
            "structure_schema": structure.get("schema"),
        },
        "areas": areas,
        "user_roles": user_roles,
        "stores": sorted(stores),
        "addons": [{"name": n, "kind": addons[n].get("kind")} for n in sorted(addons)],
        "pins": [{"name": n, "ref": pins[n].get("ref")} for n in sorted(pins)],
        "replicas": replica_status(project),
    }


def agent_view(doc: dict[str, Any]) -> dict[str, Any]:
    """Reduce a describe document to what agents need: area, role, agent keys, grade."""
    return {
        "describe_version": doc["describe_version"],
        "areas": [{"area": a["name"], "role": a["role"], "agent": a["agent"], "grade": a["grade"]}
                  for a in doc["areas"]],
    }


def _on(a: dict[str, bool]) -> str:
    return ",".join(k for k in _AGENT_KEYS if a[k]) or "none"


def render_text(doc: dict[str, Any]) -> str:
    """Render a describe document for humans."""
    p = doc["project"]
    out = [(f"project {p['name']} (spec {p['spec_version']}; config {p['config_schema']}; "
            f"structure {p['structure_schema']})"), "", "areas:"]
    for a in doc["areas"]:
        where = a["path"] if a["path"] is not None else f"store {a['store']}"
        role = a["role"] if a["role"] == a["base_role"] else f"{a['role']} (base {a['base_role']})"
        out.append(f"  {a['name']}: {where}; role {role}; {a['mutability']}; grade {a['grade']}; "
                   f"agent {_on(a['agent'])}")
    if doc["user_roles"]:
        out += ["", "user roles:"]
        out += [f"  {r['name']}: base {r['base']}; mutability {','.join(r['mutability'])}"
                + (f"; min grade {r['min_grade']}" if r["min_grade"] else "") for r in doc["user_roles"]]
    out += ["", "stores: " + (", ".join(doc["stores"]) or "none")]
    out.append("add-ons: " + (", ".join(f"{x['name']} ({x['kind']})" for x in doc["addons"]) or "none"))
    out.append("pins: " + (", ".join(f"{x['name']}={x['ref']}" for x in doc["pins"]) or "none"))
    if doc["replicas"]:
        out += ["", "replicas:"]
        for r in doc["replicas"]:
            met = "" if r["min_copies_met"] is None else f"; min_copies {r['min_copies']} " + (
                "met" if r["min_copies_met"] else "not met")
            out.append(f"  {r['area']} -> {r['store']}: last check {r['last_check'] or 'never'}; "
                       f"result {r['result'] or 'unchecked'}{met}")
    return "\n".join(out)


def render_agent_text(view: dict[str, Any]) -> str:
    """Render the agent view for humans."""
    return "\n".join(f"{a['area']}: role {a['role']}; grade {a['grade']}; agent {_on(a['agent'])}"
                     for a in view["areas"])
