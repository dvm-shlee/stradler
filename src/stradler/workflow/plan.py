"""The plan: segments, chains, input binding and the current-task rule (WORKFLOWS.md sections 5, 6, 8)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .areas import GRADES, AreaIndex, grade_rank
from .fs import LocalFS
from .identity import (
    bound_paths,
    check_unit_id,
    digest_of,
    input_components,
    input_digest,
)
from .model import Recipe, Step, StepInput
from .units import DefaultHandler, Unit, UnitSet

__all__ = ["SUPPORTED_RECORD_VERSION", "Chain", "Plan", "PlanError", "Segment", "TaskPlan", "build_plan"]

SUPPORTED_RECORD_VERSION = 1
OMIT = "omit"
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
_PATH_PH = re.compile(r"\{([^{}]*)\}")
_CMD_PH = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CMD_PH_DOTTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")


class PlanError(ValueError):
    """Raised when a plan cannot be built (bad request, not a task problem)."""


@dataclass
class TaskPlan:
    """One task of the plan.

    Attributes
    ----------
    step, level, unit : str
        Task identity (step name, level, unit identifier).
    status : str
        ``planned``, ``skip`` or ``not-run``.
    reason : str or None
        Why, when relevant.
    argv : list of str or None
        Rendered argument vector.
    bindings : dict
        Placeholder name to a bound path or list of paths.
    outputs : dict
        Output name to rendered path.
    missing : list of dict
        Upstream units a binding could not resolve (``unit``, ``step``, ``reason``).
    included, expected : int or None
        Upstream unit counts of a join.
    components : dict or None
        The four input components (section 8.2); not part of ``plan.json``.
    extra : dict
        Opaque metadata a ``task_hook`` added; written to ``plan.json`` under ``extra`` when not empty.
    """

    step: str
    level: str
    unit: str
    status: str = "planned"
    reason: str | None = None
    argv: list[str] | None = None
    bindings: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    missing: list[dict[str, str]] = field(default_factory=list)
    included: int | None = None
    expected: int | None = None
    components: dict[str, str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        """Return the task as in ``plan.json`` (section 5.5): empty optional keys are left out."""
        d: dict[str, Any] = {"step": self.step, "level": self.level, "unit": self.unit, "status": self.status}
        if self.reason:
            d["reason"] = self.reason
        if self.argv is not None:
            d["argv"] = list(self.argv)
        if self.bindings:
            d["bindings"] = self.bindings
        if self.outputs:
            d["outputs"] = self.outputs
        if self.missing:
            d["missing"] = self.missing
        if self.included is not None:
            d["included"] = self.included
            d["expected"] = self.expected
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class Chain:
    """The tasks of one unit for one segment, in recipe order.

    Attributes
    ----------
    unit : str
        Unit identifier.
    tasks : list of TaskPlan
    """

    unit: str
    tasks: list[TaskPlan]


@dataclass
class Segment:
    """A maximal run of consecutive steps with one level.

    Attributes
    ----------
    level : str
    steps : list of str
        Step names in recipe order.
    chains : list of Chain
        One per unit of the level, in identifier order.
    """

    level: str
    steps: list[str]
    chains: list[Chain]


@dataclass
class Plan:
    """The resolved list of tasks of a run.

    Attributes
    ----------
    segments : list of Segment
    named_inputs : dict
        Named inputs the run received.
    non_loop_inputs : list of dict
        Resolved fixed and named inputs (section 9.2).
    """

    segments: list[Segment]
    named_inputs: dict[str, str] = field(default_factory=dict)
    non_loop_inputs: list[dict[str, Any]] = field(default_factory=list)

    def tasks(self) -> list[TaskPlan]:
        """Return every task in segment, chain and recipe order."""
        return [t for s in self.segments for c in s.chains for t in c.tasks]

    def to_document(self) -> dict[str, Any]:
        """Return the ``plan.json`` document (section 5.5)."""
        doc: dict[str, Any] = {
            "segments": [
                {
                    "level": s.level,
                    "steps": list(s.steps),
                    "chains": [{"unit": c.unit, "tasks": [t.to_document() for t in c.tasks]} for c in s.chains],
                }
                for s in self.segments
            ]
        }
        if self.named_inputs:
            doc["named_inputs"] = dict(self.named_inputs)
        if self.non_loop_inputs:
            doc["non_loop_inputs"] = self.non_loop_inputs
        return doc

    @property
    def digest(self) -> str:
        """Digest of the canonical JSON of the plan document (``plan_digest``)."""
        return digest_of(self.to_document())


def _safe_rel(p: str) -> bool:
    return bool(p) and not p.startswith("/") and ".." not in p.split("/") and p.split("/")[0] != ".stradler"


def _template_prefix(tpl: str) -> str:
    """Return the literal folder prefix of a path template (up to the last ``/`` before the first placeholder)."""
    if "{" not in tpl:
        return tpl
    head = tpl.split("{", 1)[0]
    return head[: head.rfind("/") + 1]


def _check_grades(recipe: Recipe, steps: list[Step], named: Mapping[str, str], areas: AreaIndex) -> None:
    """Refuse a step whose outputs go to an area looser than the strictest area among its inputs.

    Areas without a ``grade`` are ``private``, so a project without grades is never refused. Inputs and outputs
    whose path lies in no area, or whose template starts with a placeholder, are not graded.
    """
    by_name = {s.name: s for s in recipe.steps}

    def entries(e: StepInput) -> list[StepInput]:
        return [e] + (entries(e.else_) if e.else_ is not None else [])

    def paths_of(e: StepInput) -> list[str]:
        if e.source == "inputs":
            return [recipe.inputs.dataset]
        if e.source == "fixed":
            return [e.path] if e.path else []
        if e.source == "named":
            return [named[e.name]] if e.name in named else []
        up = by_name.get(e.source)
        return [o.path for o in up.outputs] if up is not None else []

    for step in steps:
        ins = [(pth, areas.grade_of(_template_prefix(pth)))
               for e0 in step.inputs for e in entries(e0) for pth in paths_of(e)
               if _template_prefix(pth)]
        graded = [(pth, g) for pth, g in ins if g is not None]
        if not graded:
            continue
        strictest_path, strictest = max(graded, key=lambda t: grade_rank(t[1]))
        for o in step.outputs:
            pre = _template_prefix(o.path)
            g = areas.grade_of(pre) if pre else None
            if g is not None and grade_rank(g) < grade_rank(strictest):
                raise PlanError(
                    f"step {step.name!r}: output {o.name!r} goes to an area of grade {g!r}, looser than grade "
                    f"{strictest!r} of its input {strictest_path!r}; grades from least to most strict: "
                    f"{', '.join(GRADES)}")


class _Builder:
    def __init__(self, recipe: Recipe, root: str, areas: AreaIndex, units: UnitSet, named: Mapping[str, str],
                 force: bool, records: str | None, resolver: Any = None, namer: Any = None,
                 task_filter: Any = None, input_resolver: Any = None, task_hook: Any = None,
                 condition_resolver: Any = None, record_reader: Any = None, fs: Any = None):
        self.fs = fs if fs is not None else LocalFS(root)
        self.resolver, self.namer = resolver, namer
        self.condition_resolver, self.record_reader = condition_resolver, record_reader
        self.task_filter, self.input_resolver, self.task_hook = task_filter, input_resolver, task_hook
        self.cur_step: Step | None = None
        self.cur_values: dict[str, Any] = {}
        self._cur_entry: StepInput | None = None
        self.recipe, self.root, self.areas, self.units = recipe, root, areas, units
        self.named, self.force, self.records = dict(named), force, records
        self.tasks: dict[tuple[str, str], TaskPlan] = {}
        self.planned_outputs: set[str] = set()
        self.non_loop: dict[tuple[str, str, str], list[str]] = {}

    def exists(self, rel: str) -> bool:
        return self.fs.exists(rel)

    # -- rendering ---------------------------------------------------------------------------
    def render_outputs(self, step: Step, unit: Unit) -> tuple[dict[str, str], str | None]:
        out: dict[str, str] = {}
        for o in step.outputs:
            def sub(m: re.Match, o=o) -> str:
                ph = m.group(1)
                if self.resolver is not None:
                    v = self.resolver(ph, {"kind": "output", "step": step, "unit": unit, "output": o.name})
                    if v is not None:
                        return str(v)
                if ph == "workflow":
                    return self.recipe.name
                if ph == "unit":
                    return unit.id
                if ph == "code" and step.code:
                    return step.code
                if ph == "stem" and unit.level == "file":
                    return os.path.basename(unit.files[0]).split(".")[0]
                if ph in unit.ancestors and ph != "code":
                    return unit.ancestors[ph]
                raise KeyError(ph)
            try:
                path = _PATH_PH.sub(sub, o.path)
            except KeyError as exc:
                return {}, f"output {o.name}: unknown placeholder {{{exc.args[0]}}}"
            if self.namer is not None:
                path = str(self.namer(step, unit, o.name, path))
            if not _safe_rel(path):
                return {}, f"output {o.name}: unsafe path {path!r}"
            out[o.name] = path
        return out, None

    # -- binding -----------------------------------------------------------------------------
    def _hook_input(self, unit: Unit, e: StepInput) -> tuple[str | None, bool]:
        """Ask the ``input_resolver`` hook; return ``(path, check_exists)`` or ``(None, True)``."""
        if self.input_resolver is None or self.cur_step is None:
            return None, True
        r = self.input_resolver(self.cur_step, unit, e.as_name or "", e)
        if r is None:
            return None, True
        if isinstance(r, tuple):
            path, flags = r
            return str(path), bool((flags or {}).get("exists", True))
        return str(r), True

    def _hooked(self, unit: Unit, e: StepInput) -> tuple[Any, str | None] | None:
        p, check = self._hook_input(unit, e)
        if p is None:
            return None
        if check and not self.exists(p):
            return None, f"input missing: {p}"
        return p, None

    def _ref_value(self, src: str, unit: Unit) -> tuple[str | None, str | None]:
        t = self.tasks.get((src, unit.id))
        if t is None and self.input_resolver is not None and self._cur_entry is not None:
            hooked = self._hooked(unit, self._cur_entry)
            if hooked is not None:
                return hooked
        step = self.recipe.step(src)
        if t is not None:
            if t.status == "not-run":
                return None, "upstream task did not run"
            return next(iter(t.outputs.values())), None
        outs, err = self.render_outputs(step, unit)
        if err:
            return None, err
        first = next(iter(outs.values()))
        return (first, None) if self.exists(first) else (None, "upstream output missing")

    def _single(self, e: StepInput, unit: Unit) -> tuple[Any, str | None]:
        self._cur_entry = e
        cond = e.select.exists if e.select else None
        if cond is not None:
            met: bool | None = None
            if self.condition_resolver is not None and self.cur_step is not None:
                met = self.condition_resolver(self.cur_step, unit, cond)
                met = None if met is None else bool(met)
            if met is not None:
                pass
            elif cond in self.cur_values:
                v = self.cur_values[cond]
                met = all(self.exists(x) for x in ([v] if isinstance(v, str) else v))
            else:
                outs, err = self.render_outputs(self.recipe.step(e.source), unit)
                met = not err and cond in outs and self.exists(outs[cond])
            if not met:
                if e.else_ is not None:
                    r = self.bind(self.cur_step, unit, e.else_)  # the else entry honours its own select
                    return r["value"], r["why"]
                return None, f"condition not met: output {cond} does not exist"
        src = e.source
        if src not in ("inputs", "fixed", "named") and not any(s.name == src for s in self.recipe.steps):
            hooked = self._hooked(unit, e)
            return hooked if hooked is not None else (None, f"unknown input source: {src}")
        if src == "inputs":
            files = list(unit.files)
            return (files[0] if len(files) == 1 else files), None
        if src == "fixed":
            assert e.path is not None
            if self.exists(e.path):
                return e.path, None
            hooked = self._hooked(unit, e)  # a hook may bind a declared path that is not on disk yet
            return hooked if hooked is not None else (None, f"fixed input missing: {e.path}")
        if src == "named":
            p = self.named.get(e.name or "")
            if p is not None and not _safe_rel(p):
                hooked = self._hooked(unit, e)
                return hooked if hooked is not None else (None, f"named input has an unsafe path: {p!r}")
            if p is None:
                hooked = self._hooked(unit, e)  # a hook may supply a name the run did not receive
                return hooked if hooked is not None else (None, f"named input not given: {e.name}")
            return (p, None) if self.exists(p) else (None, f"named input missing: {p}")
        return self._ref_value(src, unit)

    def bind(self, step: Step, unit: Unit, e: StepInput) -> dict[str, Any]:
        """Resolve one entry: ``value``, ``missing``, ``included``, ``expected``, ``why``."""
        res: dict[str, Any] = {"value": None, "missing": [], "included": None, "expected": None, "why": None}
        sel = e.select
        if sel is not None and (sel.for_each or sel.pick):
            level = sel.for_each or sel.pick
            children = self.units.children(unit, level)  # type: ignore[arg-type]
            if sel.for_each:
                vals = []
                for ch in children:
                    v, why = self._single(e, ch)
                    if v is None:
                        res["missing"].append({"unit": ch.id, "step": e.source, "reason": why or "unresolved"})
                    else:
                        vals.append(v)
                res.update(value=vals, included=len(vals), expected=len(children))
            else:
                if not children:
                    res["why"] = f"no {level} units"
                else:
                    v, why = self._single(e, children[0])
                    if v is None:
                        res["missing"].append({"unit": children[0].id, "step": e.source, "reason": why or "unresolved"})
                        res["why"] = why
                    else:
                        res["value"] = v
            return res
        v, why = self._single(e, unit)
        if v is None:
            res["missing"].append({"unit": unit.id, "step": e.source, "reason": why or "unresolved"})
            res["why"] = why
        else:
            res["value"] = v
        return res

    # -- current-task rule -------------------------------------------------------------------
    def reference(self, step: str, unit: str) -> dict | None:
        if not self.records:
            return None
        base = os.path.join(self.root, self.records)
        if not self.fs.is_dir(self.records):
            return None
        for run in sorted(self.fs.list_dir(self.records), reverse=True):
            if not _RUN_ID.match(run):
                continue
            p = os.path.join(base, run, step, unit, "task.json")
            if not self.fs.is_file(os.path.join(self.records, run, step, unit, "task.json")):
                continue
            if self.record_reader is not None:
                rec = self.record_reader(p)  # the reader owns the version check and the shape
                if rec is None:
                    continue
            else:
                rec = json.loads(self.fs.read_text(os.path.join(self.records, run, step, unit, "task.json")))
                if rec.get("record_version", 1) > SUPPORTED_RECORD_VERSION:
                    raise PlanError(f"{p}: record_version {rec['record_version']} is newer than supported")
            if rec.get("status") == "ok":
                return rec
        return None

    def not_current(self, t: TaskPlan, bound: Mapping[str, Any]) -> str | None:
        ref = self.reference(t.step, t.unit)
        if ref is None:
            return "no ok record"
        old = ref.get("input_components") or {}
        new = t.components or {}
        for key, phrase in (("inputs", "inputs changed"), ("command", "parameters changed"),
                            ("container", "container changed"), ("workflow", "workflow changed")):
            if old.get(key) != new.get(key):
                return phrase
        stats = ref.get("output_stats") or {}
        for name, path in t.outputs.items():
            if stats.get(name) != self.fs.stat(path):
                return "output changed since the recorded run"
        if any(p in self.planned_outputs for p in bound_paths(bound)):
            return "upstream output regenerated"
        return None

    # -- one task ----------------------------------------------------------------------------
    def plan_task(self, step: Step, unit: Unit, chain_dead: bool) -> TaskPlan | None:
        """Plan one task; ``None`` when the task filter omits it (no task, and the chain is not killed)."""
        t = TaskPlan(step.name, step.level, unit.id)
        keep: Any = True
        why = None
        if self.task_filter is not None:
            keep = self.task_filter(step, unit)
            if isinstance(keep, tuple):
                keep, why = keep
            if isinstance(keep, str) and keep == OMIT:
                return None
        if chain_dead:
            t.status, t.reason = "not-run", "upstream task in this unit's own chain did not run"
            return t
        if not keep:
            t.status, t.reason = "not-run", why or "excluded by the task filter"
            return t
        self.cur_step = step
        outs, err = self.render_outputs(step, unit)
        if err:
            t.status, t.reason = "not-run", err
            return t
        t.outputs = outs
        outputs_exist = not self.force and all(self.exists(p) for p in outs.values())
        values: dict[str, Any] = {}
        self.cur_values = values
        hard: str | None = None
        shortfall: str | None = None
        for e in step.inputs:
            r = self.bind(step, unit, e)
            t.missing.extend(r["missing"])
            is_join = e.select is not None and bool(e.select.for_each)
            if r["value"] is not None:
                values[e.as_name or ""] = r["value"]
            if is_join:
                if t.included is None:
                    t.included, t.expected = r["included"], r["expected"]
                inc, exp = r["included"], r["expected"]
                if step.partial:
                    if inc < step.min_units and shortfall is None:
                        shortfall = f"partial aggregation below min_units ({inc}/{step.min_units})"
                        t.included, t.expected = inc, exp
                elif inc != exp and shortfall is None:
                    shortfall = f"strict barrier, {inc}/{exp} upstream units available"
                    t.included, t.expected = inc, exp
            elif r["value"] is None and hard is None:
                hard = f"missing input {e.as_name!r}: {r['why']}"
        t.bindings = values
        if shortfall:
            t.status, t.reason = "not-run", shortfall
            return t
        if hard:
            if outputs_exist:
                t.status, t.reason = "skip", "declared outputs exist; inputs not resolvable, not checked"
            else:
                t.status, t.reason = "not-run", hard
            return t
        subs: dict[str, str] = {}
        for name, v in values.items():
            subs[name] = v if isinstance(v, str) else ",".join(v)
        subs.update(outs)
        if len(outs) == 1:
            only = next(iter(outs.values()))
            subs.setdefault("output", only)
            subs.setdefault("output_dir", only)
        unresolved: list[str] = []

        def rep(m: re.Match) -> str:
            if self.resolver is not None:
                v = self.resolver(m.group(1), {"kind": "command", "step": step, "unit": unit, "bindings": values,
                                               "outputs": outs})
                if v is not None:
                    return str(v)
            if m.group(1) in subs:
                return subs[m.group(1)]
            unresolved.append(m.group(1))
            return m.group(0)

        cmd_ph = _CMD_PH if self.resolver is None else _CMD_PH_DOTTED
        argv = [cmd_ph.sub(rep, a) for a in step.command]
        if unresolved:
            t.status, t.reason = "not-run", f"unresolved placeholder: {unresolved[0]}"
            return t
        t.argv = argv
        if self.task_hook is not None:
            t.bindings = values
            t = self.task_hook(t) or t
            values = t.bindings
            if t.status != "planned":
                return t
        pin = self.recipe.containers[step.container].pin.sha256 if step.container != "none" else None
        t.components = input_components(input_digest(self.root, values), argv, pin, self.recipe.identity)
        if outputs_exist:
            why = self.not_current(t, values)
            if why is None:
                t.status, t.reason = "skip", "task is current"
            elif any(self.areas.mutability_of(p) in ("sealed", "immutable") for p in outs.values()):
                t.status = "not-run"
                t.reason = "existing output in a sealed or immutable area; it is never removed"
            else:
                t.status, t.reason = "planned", why
        else:
            t.status = "planned"
        if t.status == "planned":
            self.planned_outputs.update(outs.values())
        for e in step.inputs:
            if e.source in ("fixed", "named") and e.as_name in values:
                key = (e.as_name, e.source, values[e.as_name])
                self.non_loop.setdefault(key, []).append(unit.id)
        return t


def build_plan(
    recipe: Recipe,
    root: str | os.PathLike,
    structure: Mapping[str, Any] | AreaIndex | None = None,
    *,
    steps: Sequence[str] | None = None,
    named_inputs: Mapping[str, str] | None = None,
    force: bool = False,
    handler: Any = None,
    units: UnitSet | None = None,
    records_dir: str | None = None,
    placeholder_resolver: Any = None,
    output_namer: Any = None,
    task_filter: Any = None,
    input_resolver: Any = None,
    task_hook: Any = None,
    condition_resolver: Any = None,
    record_reader: Any = None,
    fs: Any = None,
) -> Plan:
    """Build the plan of a run. It reads files and writes nothing.

    Parameters
    ----------
    recipe : Recipe
        A validated recipe.
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex, optional
        Parsed ``structure.toml``; gives the record area and area mutability.
    steps : sequence of str, optional
        Subset of steps to run; recipe order is kept. Default: all steps.
    named_inputs : mapping, optional
        Name to project-relative path, for ``from: named`` inputs.
    force : bool
        Ignore existing outputs and records.
    handler : object, optional
        Data handler with ``discover(root, inputs)``; default :class:`DefaultHandler`.
    units : UnitSet, optional
        Units to use instead of calling the handler.
    records_dir : str, optional
        Project-relative record area; default: the area of role ``record`` in ``structure``.
    placeholder_resolver : callable, optional
        ``f(name, ctx) -> str | None``, called for a placeholder the planner does not know (``{name}`` in an
        output path, ``${name}`` in a command; with a resolver a command name may contain dots). ``ctx`` is a dict
        with ``kind`` (``output`` or ``command``), ``step``, ``unit`` and, for ``output``, ``output`` (the output
        name), for ``command``, ``bindings`` and ``outputs``. Return ``None`` to leave the name unresolved.
    output_namer : callable, optional
        ``f(step, unit, output_name, path) -> str``, called on each rendered output path before the safety check;
        returns the path to use.
    task_filter : callable, optional
        ``f(step, unit) -> bool | (bool, str)``, called for each step and unit before anything is bound. ``False``
        (or ``(False, text)``) leaves the task out: it is ``not-run`` with the reason ``text``, default
        ``excluded by the task filter``. The string ``"omit"`` (or ``("omit", text)``) leaves the task out of the
        plan altogether: no task, the chain is not cut, and a step omitted for every unit of its segment is dropped
        from ``segment.steps``.
    input_resolver : callable, optional
        ``f(step, unit, input_name, entry) -> str | (str, {"exists": bool}) | None``, called for an input entry the
        planner does not resolve itself: an unknown ``from`` kind, a reference to a step that has no task in this
        plan, or a ``named`` input whose path is not a safe relative path (with a resolver such a named path is
        not refused up front). A returned path is the bound value and must exist; return ``(path, {"exists": False})``
        to skip the existence check. ``None`` keeps the planner behaviour (the binding is unresolved).
    task_hook : callable, optional
        ``f(task) -> TaskPlan | None``, called on each task after binding, output naming and command rendering, before
        the identity components and the current-task rule. It may add to ``task.bindings`` (values are bound paths and
        enter the input digest) and to ``task.extra`` (opaque, written under ``extra`` in the plan document), or
        change ``task.status``. Returning ``None`` keeps the task passed in.
    condition_resolver : callable, optional
        ``f(step, unit, name) -> bool | None``, called for each ``select.condition.exists`` before the planner's own
        rule; ``step`` is the step being bound, ``unit`` the unit the condition applies to (a child with ``for_each``
        or ``pick``). A bool decides the condition; ``None`` keeps the planner rule.
    record_reader : callable, optional
        ``f(path) -> mapping | None``, called for each ``task.json`` the current-task rule considers, newest run
        first, instead of the built-in read and ``record_version`` check. It returns the record in the shape of
        section 9.3 (a distribution may translate its own shape), or ``None`` to ignore that file.
    fs : object, optional
        ``exists(rel)``, ``is_file(rel)``, ``is_dir(rel)`` and ``list_dir(rel)`` over project-relative paths; every
        existence or listing question of the planner goes through it. Default: ``LocalFS(root)`` (plain local files).

    Returns
    -------
    Plan

    Raises
    ------
    PlanError
        For an unknown step, an unsafe named input, a step level the handler does not declare, or a step whose
        outputs go to an area with a looser ``grade`` than the strictest area among its inputs.
    """
    root = os.fspath(root)
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    selected = list(recipe.steps)
    if steps is not None:
        names = {s.name for s in recipe.steps}
        unknown = [n for n in steps if n not in names]
        if unknown:
            raise PlanError(f"unknown steps: {', '.join(unknown)}")
        selected = [s for s in recipe.steps if s.name in set(steps)]
    named = dict(named_inputs or {})
    for n, p in named.items():
        if not _safe_rel(p) and input_resolver is None:
            raise PlanError(f"named input {n!r} has an unsafe path: {p!r}")
    _check_grades(recipe, selected, named, areas)
    if units is None:
        units = (handler or DefaultHandler()).discover(root, recipe.inputs)
    for s in selected:
        if s.level not in units.levels:
            raise PlanError(f"step {s.name!r}: level {s.level!r} is not declared by the data handler")
    b = _Builder(recipe, root, areas, units, named, force, records_dir if records_dir is not None else areas.record_path(),
               placeholder_resolver, output_namer, task_filter, input_resolver, task_hook,
               condition_resolver, record_reader, fs)
    segments: list[Segment] = []
    for step in selected:
        if segments and segments[-1].level == step.level:
            segments[-1].steps.append(step.name)
        else:
            segments.append(Segment(step.level, [step.name], []))
    by_name = {s.name: s for s in recipe.steps}
    for seg in segments:
        omitted: dict[str, int] = {}
        for unit in units.units(seg.level):
            check_unit_id(unit.id)
            chain = Chain(unit.id, [])
            dead = False
            for sname in seg.steps:
                t = b.plan_task(by_name[sname], unit, dead)
                if t is None:
                    omitted[sname] = omitted.get(sname, 0) + 1
                    continue
                b.tasks[(sname, unit.id)] = t
                chain.tasks.append(t)
                if t.status == "not-run":
                    dead = True
            seg.chains.append(chain)
        n_units = len(seg.chains)
        seg.steps = [n for n in seg.steps if not (n_units and omitted.get(n, 0) == n_units)]
    plan = Plan(segments, dict(sorted(named.items())))
    for (name, src, rel), users in sorted(b.non_loop.items()):
        uniq = sorted(set(users))
        plan.non_loop_inputs.append({
            "name": name,
            "from": src,
            "relpath": rel,
            "digest": input_digest(root, {name: rel}),
            "unit": uniq[0] if len(uniq) == 1 else None,
        })
    return plan
