"""Recipe schema: reading, validation and conversion (WORKFLOWS.md sections 3.1 to 3.10)."""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .. import yamlsubset
from .areas import AreaIndex
from .model import Recipe, build_recipe

__all__ = [
    "DEFAULT_LEVELS",
    "SCHEMA_REF",
    "Problem",
    "RecipeError",
    "convert_document",
    "derive_codes",
    "load_recipe",
    "parse_recipe",
    "validate_document",
]

SCHEMA_REF = "workflow@1.0.0"
DEFAULT_LEVELS = ("file", "group")

_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")
_INT = re.compile(r"^-?[0-9]+$")
_CODE = re.compile(r"^[1-9][A-HJ-NP-Z][0-9]{2}$")
_KIND = re.compile(r"^[A-HJ-NP-Z]$")
_MEMORY = re.compile(r"^[0-9]+(\.[0-9]+)?[KMGT]$")
_TIME = re.compile(r"^([0-9]+-)?[0-9]{1,2}:[0-9]{2}:[0-9]{2}$")
_PATH_PH = re.compile(r"\{([^{}]*)\}")
_CMD_PH = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CMD_PH_DOTTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")
_SOURCES = ("inputs", "fixed", "named")

_TOP = ("schema", "name", "family", "version", "purpose", "containers", "inputs", "steps")
_STEP = ("name", "purpose", "level", "container", "command", "in", "out", "on_incomplete", "min_units",
         "resources", "interpretation", "code", "stage_kind")


@dataclass(frozen=True)
class Problem:
    """One validation problem.

    Attributes
    ----------
    code : str
        Stable code from section 3.10, for example ``key.unknown``.
    path : str
        Location, for example ``steps[2].out[0].name``.
    message : str
        Human-readable explanation.
    """

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Return ``{"code", "path"}`` as compared by conformance cases."""
        return {"code": self.code, "path": self.path}


class RecipeError(ValueError):
    """Raised when a recipe has problems.

    Attributes
    ----------
    problems : list of Problem
        Every problem found.
    """

    def __init__(self, problems: Sequence[Problem]):
        self.problems = list(problems)
        super().__init__("; ".join(f"{p.code} at {p.path or '<root>'}: {p.message}" for p in self.problems))


class _V:
    def __init__(self, known_placeholder: Any = None) -> None:
        self.problems: list[Problem] = []
        self.known_placeholder = known_placeholder

    def err(self, code: str, path: str, msg: str) -> None:
        self.problems.append(Problem(code, path, msg))

    def mapping(self, v: Any, path: str) -> dict | None:
        if isinstance(v, dict):
            return v
        self.err("value.invalid", path, "a mapping is required")
        return None

    def keys(self, m: dict, path: str, allowed: Sequence[str], required: Sequence[str] = ()) -> None:
        for k in m:
            if k not in allowed:
                self.err("key.unknown", _j(path, k), f"key {k!r} is not defined here")
        for k in required:
            if k not in m:
                self.err("key.missing", _j(path, k), f"required key {k!r} is absent")

    def string(self, v: Any, path: str, pattern: re.Pattern | None = None, what: str = "a non-empty string") -> str | None:
        if not isinstance(v, str) or v == "" or (pattern is not None and not pattern.match(v)):
            self.err("value.invalid", path, f"{what} is required")
            return None
        return v

    def integer(self, v: Any, path: str, minimum: int) -> int | None:
        if not isinstance(v, str) or not _INT.match(v) or int(v) < minimum:
            self.err("value.invalid", path, f"an integer >= {minimum} is required")
            return None
        return int(v)

    def string_list(self, v: Any, path: str, nonempty: bool = False) -> list[str] | None:
        if not isinstance(v, list) or (nonempty and not v) or not all(isinstance(x, str) for x in v):
            self.err("value.invalid", path, "a list of strings is required")
            return None
        return v


def _j(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _unsafe(p: str) -> str | None:
    if p == "" or p.startswith("/"):
        return "absolute or empty path"
    segs = p.split("/")
    if ".." in segs:
        return "path contains '..'"
    if segs[0] == ".stradler":
        return "path lies inside .stradler/"
    return None


def _safe(v: _V, value: Any, path: str) -> str | None:
    s = v.string(value, path)
    if s is None:
        return None
    why = _unsafe(s)
    if why:
        v.err("path.unsafe", path, why)
        return None
    return s


def derive_codes(steps: Sequence[Mapping[str, Any]]) -> tuple[dict[int, str], list[Problem]]:
    """Derive step codes from ``stage_kind`` declarations (section 3.9).

    Parameters
    ----------
    steps : sequence of mapping
        Raw step mappings in recipe order.

    Returns
    -------
    tuple
        ``(codes, problems)``: step index to code (declared codes included), and ``code.derive`` problems.
    """
    problems: list[Problem] = []
    codes: dict[int, str] = {}
    for i, s in enumerate(steps):
        if isinstance(s, dict) and isinstance(s.get("code"), str):
            codes[i] = s["code"]
    if not any(isinstance(s, dict) and "stage_kind" in s for s in steps):
        return codes, problems
    stage_of: dict[str, int] = {}
    count: dict[str, int] = {}
    prev = None
    derived: dict[int, str] = {}
    for i, s in enumerate(steps):
        kind = s.get("stage_kind") if isinstance(s, dict) else None
        if not isinstance(kind, str) or not _KIND.match(kind):
            problems.append(Problem("code.derive", f"steps[{i}].stage_kind",
                                    "every step needs a valid stage_kind when any step declares one"))
            prev = None
            continue
        if kind not in stage_of:
            if len(stage_of) >= 9:
                problems.append(Problem("code.derive", f"steps[{i}].stage_kind", "more than 9 stages"))
                continue
            stage_of[kind] = len(stage_of) + 1
        elif prev != kind:
            problems.append(Problem("code.derive", f"steps[{i}].stage_kind", f"steps of kind {kind} are not contiguous"))
        prev = kind
        count[kind] = count.get(kind, 0) + 1
        if count[kind] > 99:
            problems.append(Problem("code.derive", f"steps[{i}].stage_kind", "more than 99 substeps"))
            continue
        derived[i] = f"{stage_of[kind]}{kind}{count[kind]:02d}"
    for i, c in derived.items():
        declared = steps[i].get("code")
        if declared is not None and declared != c:
            problems.append(Problem("code.derive", f"steps[{i}].code", f"declared code {declared!r} must equal derived {c!r}"))
        codes[i] = c
    return codes, problems


def validate_document(
    doc: Any,
    *,
    areas: AreaIndex | Mapping[str, Any] | None = None,
    levels: Sequence[str] = DEFAULT_LEVELS,
    known_placeholder: Any = None,
) -> list[Problem]:
    """Validate a parsed recipe document and report every problem.

    Parameters
    ----------
    doc : object
        Result of the YAML subset reader.
    areas : AreaIndex or mapping, optional
        Project structure (parsed ``structure.toml``) for the path checks; omitted, the area checks are skipped.
    levels : sequence of str
        Levels of the dataset's handler, finest first, starting with ``file`` and ending with ``group``.
    known_placeholder : callable, optional
        ``f(name) -> bool``; a path or command placeholder for which it returns true is accepted (the distribution
        resolves it at plan time). Command names may then contain dots.

    Returns
    -------
    list of Problem
        Empty when the recipe is valid.
    """
    if areas is not None and not isinstance(areas, AreaIndex):
        areas = AreaIndex(areas)
    levels = tuple(levels)
    v = _V(known_placeholder)
    if not isinstance(doc, dict):
        v.err("value.invalid", "", "the recipe must be a mapping")
        return v.problems
    v.keys(doc, "", _TOP, ("schema", "name", "family", "version", "purpose", "inputs", "steps"))
    if "schema" in doc and doc["schema"] != SCHEMA_REF:
        v.err("value.invalid", "schema", f"must be {SCHEMA_REF}")
    family = doc.get("family")
    family = v.string(family, "family", _ID, "an identifier") if "family" in doc else None
    name = v.string(doc["name"], "name") if "name" in doc else None
    if name is not None and family is not None and not (name.startswith(family + ".") and len(name) > len(family) + 1):
        v.err("name.prefix", "name", f"name must begin with {family!r} and '.'")
    if "version" in doc:
        v.string(doc["version"], "version", _VERSION, "MAJOR.MINOR.PATCH")
    if "purpose" in doc:
        v.string(doc["purpose"], "purpose")
    containers = _containers(v, doc.get("containers"), areas) if "containers" in doc else {}
    if "inputs" in doc:
        _inputs(v, doc["inputs"], levels, areas)
    steps = doc.get("steps")
    if "steps" in doc:
        if not isinstance(steps, list) or not steps:
            v.err("value.invalid", "steps", "a non-empty list is required")
        else:
            _steps(v, steps, containers, levels, areas, str(doc.get("name", "")))
    return v.problems


def _containers(v: _V, raw: Any, areas: AreaIndex | None) -> set[str]:
    names: set[str] = set()
    m = v.mapping(raw, "containers")
    if m is None:
        return names
    for cname, c in m.items():
        path = f"containers.{cname}"
        if not _ID.match(cname) or cname == "none":
            v.err("value.invalid", path, "container name must match [A-Za-z0-9_-]+ and not be 'none'")
        names.add(cname)
        c = v.mapping(c, path)
        if c is None:
            continue
        kind = c.get("kind", "recipe")
        if kind not in ("recipe", "image"):
            v.err("value.invalid", _j(path, "kind"), "must be 'recipe' or 'image'")
            kind = "recipe"
        allowed = ["kind", "pin", "recipe" if kind == "recipe" else "image"]
        v.keys(c, path, allowed, ("pin", "recipe" if kind == "recipe" else "image"))
        if "recipe" in c and kind == "recipe":
            p = _safe(v, c["recipe"], _j(path, "recipe"))
            if p and areas is not None and not areas.has_role(p, ("recipe", "code")):
                v.err("path.area", _j(path, "recipe"), "must lie in an area of role recipe or code")
        if "image" in c and kind == "image":
            v.string(c["image"], _j(path, "image"))
        if "pin" in c:
            pin = v.mapping(c["pin"], _j(path, "pin"))
            if pin is not None:
                v.keys(pin, _j(path, "pin"), ("version", "sha256", "path"), ("version", "sha256"))
                if "version" in pin:
                    v.string(pin["version"], _j(path, "pin.version"))
                if "sha256" in pin:
                    v.string(pin["sha256"], _j(path, "pin.sha256"), _SHA, "64 hexadecimal digits")
                if "path" in pin:
                    _safe(v, pin["path"], _j(path, "pin.path"))
    return names


def _inputs(v: _V, raw: Any, levels: tuple[str, ...], areas: AreaIndex | None) -> None:
    m = v.mapping(raw, "inputs")
    if m is None:
        return
    v.keys(m, "inputs", ("dataset", "filter", "units"), ("dataset",))
    if "dataset" in m:
        p = _safe(v, m["dataset"], "inputs.dataset")
        if p and areas is not None and areas.area_of(p) is None:
            v.err("path.area", "inputs.dataset", "the dataset folder must lie in an area")
    if "filter" in m:
        f = v.mapping(m["filter"], "inputs.filter")
        for k, val in (f or {}).items():
            if not (isinstance(val, str) and val) and not (isinstance(val, list) and all(isinstance(x, str) for x in val)):
                v.err("value.invalid", f"inputs.filter.{k}", "a string or a list of strings is required")
    if "units" in m:
        u = v.mapping(m["units"], "inputs.units")
        for lvl, sel in (u or {}).items():
            path = f"inputs.units.{lvl}"
            if lvl not in levels[1:-1]:
                v.err("ref.unknown", path, f"{lvl!r} is not an intermediate level of the handler")
            sm = v.mapping(sel, path)
            if sm is None:
                continue
            v.keys(sm, path, ("include", "exclude"))
            for k in ("include", "exclude"):
                if k in sm and sm[k] != "*":
                    v.string_list(sm[k], _j(path, k))


def _check_path_template(v: _V, tpl: str, path: str, step: dict, level: str, levels: tuple[str, ...],
                         has_code: bool, areas: AreaIndex | None) -> None:
    why = _unsafe(tpl)
    if why:
        v.err("path.unsafe", path, why)
        return
    coarser = levels[levels.index(level):] if level in levels else ()
    for ph in _PATH_PH.findall(tpl):
        ok = (ph in ("workflow", "unit")
              or (ph == "code" and has_code)
              or (ph == "stem" and level == "file")
              or ph in coarser)
        if not ok and v.known_placeholder is not None and v.known_placeholder(ph):
            ok = True
        if not ok:
            v.err("placeholder.unknown", path, f"placeholder {{{ph}}} is not defined for this step")
    if areas is not None and not areas.has_role(tpl, ("output", "record")):
        v.err("path.area", path, "must lie in an area of role output or record")


def _steps(v: _V, steps: list, containers: set[str], levels: tuple[str, ...], areas: AreaIndex | None,
           wf_name: str) -> None:
    index: dict[str, int] = {}
    level_of: dict[str, str] = {}
    outs_of: dict[str, set[str]] = {}
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        n = s.get("name")
        if isinstance(n, str) and _ID.match(n):
            if n in index:
                v.err("name.duplicate", f"steps[{i}].name", f"step name {n!r} is used twice")
            else:
                index[n] = i
                if isinstance(s.get("level"), str):
                    level_of[n] = s["level"]
                outs_of[n] = {o.get("name") for o in s.get("out", []) if isinstance(o, dict)} if isinstance(s.get("out"), list) else set()
    codes, problems = derive_codes([s if isinstance(s, dict) else {} for s in steps])
    v.problems.extend(problems)
    seen_codes: dict[str, int] = {}
    for i, c in sorted(codes.items()):
        if c in seen_codes:
            v.err("name.duplicate", f"steps[{i}].code", f"code {c!r} is used twice")
        seen_codes[c] = i
    for i, s in enumerate(steps):
        path = f"steps[{i}]"
        s = v.mapping(s, path)
        if s is None:
            continue
        v.keys(s, path, _STEP, ("name", "purpose", "level", "container", "command", "out"))
        n = s.get("name")
        if "name" in s and v.string(n, _j(path, "name"), _ID, "an identifier") and n in _SOURCES:
            v.err("value.invalid", _j(path, "name"), f"step name {n!r} is reserved")
        if "purpose" in s:
            v.string(s["purpose"], _j(path, "purpose"))
        level = s.get("level")
        if "level" in s and not (isinstance(level, str) and level in levels):
            v.err("value.invalid", _j(path, "level"), f"level must be one of {', '.join(levels)}")
            level = None
        if "container" in s:
            c = s["container"]
            if not isinstance(c, str) or c == "" or (c != "none" and c not in containers):
                v.err("ref.unknown", _j(path, "container"), "must be 'none' or a name in containers")
        if "command" in s:
            cmd = v.string_list(s["command"], _j(path, "command"), nonempty=True)
        else:
            cmd = None
        if "on_incomplete" in s and s["on_incomplete"] != "partial":
            v.err("value.invalid", _j(path, "on_incomplete"), "must be 'partial'")
        if "min_units" in s:
            if "on_incomplete" not in s:
                v.err("key.unknown", _j(path, "min_units"), "allowed only with on_incomplete")
            else:
                v.integer(s["min_units"], _j(path, "min_units"), 0)
        if "resources" in s:
            _resources(v, s["resources"], _j(path, "resources"))
        if "interpretation" in s:
            v.string(s["interpretation"], _j(path, "interpretation"))
        if "code" in s and v.string(s["code"], _j(path, "code")) and not _CODE.match(s["code"]):
            v.err("code.format", _j(path, "code"), "must match [1-9][A-HJ-NP-Z][0-9]{2}")
        if "stage_kind" in s and isinstance(s["stage_kind"], str) and not _KIND.match(s["stage_kind"]):
            v.err("code.format", _j(path, "stage_kind"), "must be one uppercase letter other than I and O")
        as_names: list[str] = []
        if "in" in s:
            if not isinstance(s["in"], list):
                v.err("value.invalid", _j(path, "in"), "a list is required")
            else:
                for k, e in enumerate(s["in"]):
                    _input(v, e, f"{path}.in[{k}]", i, level, steps, index, level_of, outs_of, levels, as_names, False)
        out_names: list[str] = []
        if "out" in s:
            if not isinstance(s["out"], list) or not s["out"]:
                v.err("value.invalid", _j(path, "out"), "a non-empty list is required")
            else:
                for k, o in enumerate(s["out"]):
                    opath = f"{path}.out[{k}]"
                    o = v.mapping(o, opath)
                    if o is None:
                        continue
                    v.keys(o, opath, ("name", "path"), ("name", "path"))
                    on = o.get("name")
                    if "name" in o and v.string(on, _j(opath, "name"), _IDENT, "an identifier"):
                        if on in out_names or on in as_names:
                            v.err("name.duplicate", _j(opath, "name"), f"name {on!r} is used twice in the step")
                        out_names.append(on)
                    if "path" in o and v.string(o["path"], _j(opath, "path")) and level:
                        _check_path_template(v, o["path"], _j(opath, "path"), s, level, levels, i in codes, areas)
        if cmd:
            known = set(as_names) | set(out_names)
            if len(out_names) == 1:
                known |= {"output", "output_dir"}
            for j, arg in enumerate(cmd):
                for ph in _CMD_PH_DOTTED.findall(arg):
                    if ph not in known and not (v.known_placeholder is not None and v.known_placeholder(ph)):
                        v.err("placeholder.unknown", f"{path}.command[{j}]", f"placeholder ${{{ph}}} is not defined")


def _resources(v: _V, raw: Any, path: str) -> None:
    m = v.mapping(raw, path)
    if m is None:
        return
    v.keys(m, path, ("cpus", "memory", "time", "gpus"))
    if "cpus" in m:
        v.integer(m["cpus"], _j(path, "cpus"), 1)
    if "gpus" in m:
        v.integer(m["gpus"], _j(path, "gpus"), 0)
    if "memory" in m:
        v.string(m["memory"], _j(path, "memory"), _MEMORY, "a number followed by K, M, G or T")
    if "time" in m:
        v.string(m["time"], _j(path, "time"), _TIME, "[D-]HH:MM:SS")


def _input(v: _V, raw: Any, path: str, si: int, level: str | None, steps: list, index: dict[str, int],
           level_of: dict[str, str], outs_of: dict[str, set[str]], levels: tuple[str, ...],
           as_names: list[str], is_else: bool) -> None:
    e = v.mapping(raw, path)
    if e is None:
        return
    prior_names = list(as_names)
    allowed = ["from", "path", "name", "select", "else"] + ([] if is_else else ["as"])
    v.keys(e, path, allowed, ["from"] + ([] if is_else else ["as"]))
    if "as" in e and not is_else:
        a = v.string(e["as"], _j(path, "as"), _IDENT, "an identifier")
        if a:
            if a in as_names:
                v.err("name.duplicate", _j(path, "as"), f"placeholder {a!r} is used twice in the step")
            as_names.append(a)
    src = e.get("from")
    if "from" not in e:
        return
    if not isinstance(src, str) or src == "":
        v.err("value.invalid", _j(path, "from"), "a non-empty string is required")
        return
    if src == "fixed":
        if "path" not in e:
            v.err("key.missing", _j(path, "path"), "required for from: fixed")
        else:
            _safe(v, e["path"], _j(path, "path"))
    elif "path" in e:
        v.err("key.unknown", _j(path, "path"), "allowed only for from: fixed")
    if src == "named":
        if "name" not in e:
            v.err("key.missing", _j(path, "name"), "required for from: named")
        else:
            v.string(e["name"], _j(path, "name"), _IDENT, "an identifier")
    elif "name" in e:
        v.err("key.unknown", _j(path, "name"), "allowed only for from: named")
    is_step = src not in _SOURCES
    ref_level = None
    if is_step:
        if src not in index:
            v.err("ref.unknown", _j(path, "from"), f"no step named {src!r}")
        elif index[src] >= si:
            v.err("ref.order", _j(path, "from"), f"step {src!r} does not precede this step")
        else:
            ref_level = level_of.get(src)
    elif "select" in e:
        v.err("key.unknown", _j(path, "select"), "select is allowed only when from names a step")
    sel_level = level
    if "select" in e and is_step:
        spath = _j(path, "select")
        sel = v.mapping(e["select"], spath)
        if sel is not None:
            v.keys(sel, spath, ("for_each", "pick", "condition"))
            if not any(k in sel for k in ("for_each", "pick", "condition")):
                v.err("key.missing", spath, "select needs for_each, pick or condition")
            if "for_each" in sel and "pick" in sel:
                v.err("value.invalid", spath, "for_each and pick are mutually exclusive")
            agg = None
            if "for_each" in sel:
                agg = v.string(sel["for_each"], _j(spath, "for_each"))
            elif "pick" in sel:
                p = v.string(sel["pick"], _j(spath, "pick"))
                if p is not None:
                    if p.startswith("first_"):
                        agg = p[len("first_"):]
                    else:
                        v.err("value.invalid", _j(spath, "pick"), "must be first_<level>")
            if agg is not None:
                if agg not in levels:
                    v.err("value.invalid", _j(spath, "for_each" if "for_each" in sel else "pick"), f"unknown level {agg!r}")
                else:
                    sel_level = agg
                    if level and levels.index(agg) >= levels.index(level):
                        v.err("ref.level", _j(spath, "for_each" if "for_each" in sel else "pick"),
                              "the level must be finer than the step's level")
            if "condition" in sel:
                c = v.mapping(sel["condition"], _j(spath, "condition"))
                if c is not None:
                    v.keys(c, _j(spath, "condition"), ("exists",), ("exists",))
                    ex = c.get("exists")
                    if "exists" in c and src in outs_of and ex not in outs_of[src] and ex not in prior_names:
                        v.err("ref.unknown", _j(spath, "condition.exists"), f"step {src!r} has no output {ex!r} and no earlier input {ex!r}")
    if is_step and ref_level is not None and sel_level is not None and ref_level != sel_level:
        v.err("ref.level", _j(path, "from"),
              f"step {src!r} has level {ref_level!r}, but this binding needs {sel_level!r}")
    if "else" in e:
        _input(v, e["else"], _j(path, "else"), si, level, steps, index, level_of, outs_of, levels, as_names, True)


def convert_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Convert integer fields of a valid document from strings to integers (section 3.1).

    Parameters
    ----------
    doc : mapping
        A document that passed :func:`validate_document`.

    Returns
    -------
    dict
        A deep copy where ``min_units``, ``resources.cpus`` and ``resources.gpus`` are integers.
    """
    out = copy.deepcopy(dict(doc))
    for s in out.get("steps", []):
        if "min_units" in s:
            s["min_units"] = int(s["min_units"])
        for k in ("cpus", "gpus"):
            if k in s.get("resources", {}):
                s["resources"][k] = int(s["resources"][k])
    return out


def parse_recipe(
    text: str,
    *,
    areas: AreaIndex | Mapping[str, Any] | None = None,
    levels: Sequence[str] = DEFAULT_LEVELS,
    known_placeholder: Any = None,
) -> Recipe:
    """Read and validate a recipe from YAML text.

    Parameters
    ----------
    text : str
        Recipe text in the strict YAML subset.
    areas : AreaIndex or mapping, optional
        Project structure for the path checks.
    levels : sequence of str
        Levels of the dataset's handler, finest first.
    known_placeholder : callable, optional
        ``f(name) -> bool``: placeholders the distribution declares (see :func:`validate_document`).

    Returns
    -------
    Recipe

    Raises
    ------
    RecipeError
        With every problem found; ``yaml.syntax`` or ``yaml.unsupported`` for text the reader refuses.
    """
    try:
        doc = yamlsubset.loads(text)
    except yamlsubset.YamlSubsetError as exc:
        code = "yaml.unsupported" if "not supported" in str(exc) else "yaml.syntax"
        raise RecipeError([Problem(code, "", str(exc))]) from exc
    problems = validate_document(doc, areas=areas, levels=levels, known_placeholder=known_placeholder)
    if problems:
        raise RecipeError(problems)
    codes, _ = derive_codes(doc["steps"])
    return build_recipe(convert_document(doc), codes)


def load_recipe(
    path: str | os.PathLike,
    *,
    areas: AreaIndex | Mapping[str, Any] | None = None,
    levels: Sequence[str] = DEFAULT_LEVELS,
    known_placeholder: Any = None,
) -> Recipe:
    """Read and validate a recipe file.

    Parameters
    ----------
    path : str or os.PathLike
        Recipe file (UTF-8).
    areas : AreaIndex or mapping, optional
        Project structure for the path checks.
    levels : sequence of str
        Levels of the dataset's handler, finest first.

    Returns
    -------
    Recipe

    Raises
    ------
    RecipeError
        As :func:`parse_recipe`.
    """
    with open(path, encoding="utf-8") as fh:
        return parse_recipe(fh.read(), areas=areas, levels=levels, known_placeholder=known_placeholder)
