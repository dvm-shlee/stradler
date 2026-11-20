"""Executor with the built-in local scheduler and the no-container runtime (WORKFLOWS.md sections 7 and 9)."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import __version__
from .areas import AreaIndex
from .fs import LocalFS
from .identity import (
    bound_paths,
    combine_components,
    digest_of,
    sha256_digest,
    task_record_dir,
)
from .model import Recipe
from .plan import Plan, TaskPlan, build_plan

__all__ = ["RunResult", "new_run_id", "run_workflow", "write_json_atomic"]

RECORD_VERSION = 1
DEFAULT_RECORD_AREA = "records"
WARNING_PREFIX = "stradler-warning: "


@dataclass
class RunResult:
    """Outcome of a run.

    Attributes
    ----------
    run_id : str
    run_dir : str
        Absolute path of the run directory.
    status : str
        ``ok``, ``failed`` or ``interrupted``.
    plan : Plan
    tasks : dict
        ``(step, unit)`` to the task record written.
    """

    run_id: str
    run_dir: str
    status: str
    plan: Plan
    tasks: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """``0`` when the run is ``ok``, else ``1`` (section 9.2)."""
        return 0 if self.status == "ok" else 1

    def count(self, status: str) -> int:
        """Number of task records with ``status``."""
        return sum(1 for r in self.tasks.values() if r["status"] == status)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_run_id() -> str:
    """Return a run id ``YYYYMMDDTHHMMSSZ-<8 hex>`` in UTC (section 9.1)."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)


def write_json_atomic(path: str, doc: Any) -> None:
    """Write ``doc`` as JSON to a temporary name, then rename it into place (section 9.1).

    Parameters
    ----------
    path : str
        Destination file; its directory is created.
    doc : object
        JSON-serialisable value.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _git(full: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", full, *args], capture_output=True, text=True, check=False)


def _dataset_entry(root: str, areas: AreaIndex, path: str, fs: Any) -> dict[str, Any]:
    hit = areas.area_of(path)
    e: dict[str, Any] = {"path": path, "role": hit[1].get("role", "") if hit else "", "revision": None, "dirty": None}
    full = os.path.join(root, path)
    links: list[list[str]] = []
    for dirpath, dirs, files in os.walk(full):
        for n in dirs + files:
            p = os.path.join(dirpath, n)
            if fs.is_link(os.path.relpath(p, root).replace(os.sep, "/")):
                links.append([os.path.relpath(p, root).replace(os.sep, "/"), os.readlink(p)])
    try:
        top = _git(full, "rev-parse", "--show-toplevel") if fs.is_dir(path) else None
        if top is not None and top.returncode == 0:
            repo = os.path.realpath(top.stdout.strip())
            rev = _git(full, "rev-parse", "HEAD")
            if rev.returncode == 0:
                e["revision"] = rev.stdout.strip()
                e["dirty"] = bool(_git(full, "status", "--porcelain", "--", ".").stdout.strip())
            else:
                e["reason"] = "repository has no commit"
            rel = os.path.relpath(repo, os.path.realpath(root)).replace(os.sep, "/")
            if rel not in (path, "."):
                e["repo"] = rel
        else:
            e["reason"] = "no version control repository"
    except OSError:
        e["reason"] = "git not available"
    if links:
        links.sort()
        e["links"] = len(links)
        e["link_digest"] = digest_of(links)
    return e


def _container_object(recipe: Recipe, name: str, wrapped: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if name == "none":
        return None
    if wrapped is not None:
        return dict(wrapped["container"])
    c = recipe.containers[name]
    o: dict[str, Any] = {"name": name, "kind": c.kind, "pin_digest": "sha256:" + c.pin.sha256}
    if c.pin.placeholder:
        o["pin_placeholder"] = True
    return o


def _warnings(fs: Any, root: str, *logs: str) -> list[str]:
    out: list[str] = []
    for p in logs:
        if not fs.is_file(os.path.relpath(p, root)):
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(WARNING_PREFIX):
                    w = line[len(WARNING_PREFIX):].strip()
                    if w not in out:
                        out.append(w)
    return out


def _ensure_output_dirs(root: str, outputs: Mapping[str, str]) -> None:
    """Create each directory output (trailing slash or ancestor of another output) and each file output's parent."""
    norm = [os.path.normpath(p) for p in outputs.values()]
    for raw, n in zip(outputs.values(), norm):
        is_dir = raw.endswith("/") or any(o != n and o.startswith(n + os.sep) for o in norm)
        target = n if is_dir else os.path.dirname(n)
        if target:
            os.makedirs(os.path.join(root, target), exist_ok=True)


def _remove(root: str, rel: str, fs: Any) -> None:
    full = os.path.join(root, rel)
    if fs.is_dir(rel) and not fs.is_link(rel):
        shutil.rmtree(full)
    else:
        os.remove(full)


class _Run:
    def __init__(self, recipe: Recipe, root: str, run_dir: str, reproduces: Mapping[str, str] | None, fs: Any):
        self.fs = fs
        self.recipe, self.root, self.run_dir, self.reproduces = recipe, root, run_dir, reproduces
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.dead: set[str] = set()  # output paths of tasks that did not end ok
        self.wrapped: dict[str, dict[str, Any]] = {}  # "step<TAB>unit" to {"argv", "container"} (runtime add-on)

    def base_record(self, t: TaskPlan, status: str) -> dict[str, Any]:
        step = self.recipe.step(t.step)
        r: dict[str, Any] = {
            "record_version": RECORD_VERSION, "step": t.step, "unit": t.unit, "level": t.level,
            "purpose": step.purpose, "status": status,
            "container": _container_object(self.recipe, step.container, self.wrapped.get(f"{t.step}\t{t.unit}")),
            "stradler_version": __version__, "stradler_revision": None,
        }
        if step.interpretation:
            r["interpretation"] = step.interpretation
        if step.code:
            r["step_code"] = step.code
        if t.argv is not None:
            r["argv"] = list(t.argv)
        if t.components is not None:
            r["input_components"] = dict(t.components)
            r["input_digest"] = combine_components(t.components)
        if t.included is not None:
            r["included"], r["expected"] = t.included, t.expected
        if t.outputs:
            r["outputs"] = dict(t.outputs)
        if self.reproduces:
            r["reproduces"] = dict(self.reproduces)
        return r

    def put(self, t: TaskPlan, rec: dict[str, Any]) -> None:
        self.records[(t.step, t.unit)] = rec
        write_json_atomic(os.path.join(task_record_dir(self.run_dir, t.step, t.unit), "task.json"), rec)

    def settle(self, t: TaskPlan, status: str, reason: str) -> None:
        rec = self.base_record(t, status)
        rec["reason"] = reason
        if status == "not-run":
            self.dead.update(t.outputs.values())
        self.put(t, rec)

    def execute(self, t: TaskPlan) -> None:
        step = self.recipe.step(t.step)
        wrapped = self.wrapped.get(f"{t.step}\t{t.unit}")
        if step.container != "none" and wrapped is None:
            self.settle(t, "not-run", "no container runtime available for this scheduler")
            return
        if any(p in self.dead for p in bound_paths(t.bindings)):
            self.settle(t, "not-run", "upstream task did not run")
            return
        d = task_record_dir(self.run_dir, t.step, t.unit)
        os.makedirs(d, exist_ok=True)
        running = os.path.join(d, ".running")
        started = _now()
        with open(running, "w", encoding="utf-8") as fh:
            fh.write(started + "\n")
        removed: list[str] = []
        for p in t.outputs.values():
            if self.fs.lexists(p):
                _remove(self.root, p, self.fs)
                removed.append(p)
        _ensure_output_dirs(self.root, t.outputs)
        out_log, err_log = os.path.join(d, "stdout.log"), os.path.join(d, "stderr.log")
        rec = self.base_record(t, "failed")
        rec["started"] = started
        if removed:
            rec["removed_outputs"] = removed
        assert t.argv is not None
        argv = wrapped["argv"] if wrapped is not None else t.argv
        try:
            with open(out_log, "wb") as so, open(err_log, "wb") as se:
                code = subprocess.run(argv, cwd=self.root, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                      check=False).returncode
        except OSError as exc:
            code = 127
            with open(err_log, "w", encoding="utf-8") as se:
                se.write(f"{exc}\n")
            rec["reason"] = f"cannot start command: {exc.strerror or exc}"
        except KeyboardInterrupt:
            rec.update(ended=_now(), exit_code=-2, reason="cancelled")
            self.dead.update(t.outputs.values())
            self.put(t, rec)
            os.remove(running)
            raise
        rec["ended"], rec["exit_code"] = _now(), code
        if code == 0:
            missing = [n for n, p in t.outputs.items() if not self.fs.exists(p)]
            if missing:
                rec["reason"] = "declared output missing"
            else:
                rec["status"] = "ok"
                rec["output_stats"] = {n: self.fs.stat(p) for n, p in t.outputs.items()}
        elif "reason" not in rec:
            rec["reason"] = f"exit code {code}"
        warns = _warnings(self.fs, self.root, out_log, err_log)
        if warns:
            rec["warnings"] = warns
        if rec["status"] != "ok":
            self.dead.update(t.outputs.values())
        self.put(t, rec)
        os.remove(running)


def run_workflow(
    recipe: Recipe,
    root: str | os.PathLike,
    structure: Mapping[str, Any] | AreaIndex | None = None,
    *,
    steps: Sequence[str] | None = None,
    named_inputs: Mapping[str, str] | None = None,
    force: bool = False,
    handler: Any = None,
    recipe_file: str | None = None,
    reproduces: Mapping[str, str] | None = None,
    fs: Any = None,
    addons: Mapping[str, Any] | None = None,
    scheduler: str | None = None,
    runtime: str | None = None,
    job_prefix: str = "stradler",
) -> RunResult:
    """Plan and run a workflow with the built-in local scheduler.

    Tasks run one after another in plan order, as argument vectors without a shell, with the project root as
    working directory. Current tasks are skipped (section 8). A run record is written under the record area
    (section 9). A task whose container is not ``none`` is ``not-run`` until a container runtime add-on exists.

    Parameters
    ----------
    recipe : Recipe
        A validated recipe.
    root : str or os.PathLike
        Project root.
    structure : mapping or AreaIndex, optional
        Parsed ``structure.toml``.
    steps, named_inputs, force, handler
        As for :func:`stradler.workflow.build_plan`.
    recipe_file : str, optional
        Project-relative path of the recipe file, recorded with its digest.
    reproduces : mapping, optional
        ``{run, step, unit}`` recorded in each task as ``reproduces`` (section 7.3).
    fs : object, optional
        As for ``build_plan``; also used for the declared-output check and the removal of stale outputs. Default
        ``LocalFS(root)``.
    addons : mapping, optional
        Add-ons by name (:func:`stradler.addons.load_addons`).
    scheduler : str, optional
        Name of a scheduler add-on. Without it the built-in local scheduler runs the tasks and blocks. With it the
        run submits jobs and returns at once (ADDONS 2, 3): ``status`` is ``running`` for a non-blocking
        scheduler. ``recipe_file`` is required, and a custom ``fs`` is not seen by the jobs.
    runtime : str, optional
        Container runtime add-on of a scheduler run; default the only configured one.
    job_prefix : str
        Job-name prefix of the submitted jobs.

    Returns
    -------
    RunResult
        ``status`` is ``interrupted`` when the run was cancelled by ``KeyboardInterrupt``.

    Raises
    ------
    PlanError
        When the plan cannot be built.
    """
    root = os.fspath(root)
    fs = fs if fs is not None else LocalFS(root)
    areas = structure if isinstance(structure, AreaIndex) else AreaIndex(structure)
    rec_area = areas.record_path() or DEFAULT_RECORD_AREA
    plan = build_plan(recipe, root, areas, steps=steps, named_inputs=named_inputs, force=force, handler=handler,
                      records_dir=rec_area, fs=fs)
    prep = None
    if scheduler is not None:
        from . import scheduled
        prep = scheduled.prepare(recipe, root, areas, plan, addons or {}, scheduler, runtime, job_prefix, recipe_file)
    run_id = new_run_id()
    run_dir = os.path.join(root, rec_area, run_id)
    os.makedirs(run_dir)
    run = _Run(recipe, root, run_dir, reproduces, fs)
    write_json_atomic(os.path.join(run_dir, "plan.json"), plan.to_document())
    file_digest = None
    if recipe_file:
        with open(os.path.join(root, recipe_file), "rb") as fh:
            file_digest = sha256_digest(fh.read())
    run_doc: dict[str, Any] = {
        "record_version": RECORD_VERSION, "run_id": run_id,
        "workflow": {
            "name": recipe.name, "family": recipe.family, "version": recipe.version, "file": recipe_file,
            "file_digest": file_digest, "content_digest": recipe.content_digest,
            "inputs_digest": recipe.inputs_digest,
        },
        "inputs": {"datasets": [_dataset_entry(root, areas, recipe.inputs.dataset, fs)]},
        "backend": "local", "plan_digest": plan.digest, "stradler_version": __version__,
        "stradler_revision": None, "non_loop_inputs": plan.non_loop_inputs,
        "segments": [{"level": s.level, "steps": list(s.steps), "job_name": None, "job_id": None,
                      "array_size": None} for s in plan.segments],
        "status": "running", "started": _now(), "ended": None, "exit_code": 1,
    }
    codes = {s.name: s.code for s in recipe.steps if s.code}
    if codes:
        run_doc["step_codes"] = codes
    write_json_atomic(os.path.join(run_dir, "run.json"), run_doc)

    todo: list[TaskPlan] = []
    for t in plan.tasks():
        if t.status in ("skip", "not-run"):
            run.settle(t, "skipped" if t.status == "skip" else "not-run", t.reason or t.status)
        else:
            todo.append(t)
    if prep is not None:
        from . import scheduled
        final = scheduled.submit_run(prep, root, run_dir, run_doc, run_id)
        run.records = scheduled.read_records(run_dir)
        return RunResult(run_id, run_dir, final["status"], plan, run.records)
    status = "ok"
    try:
        for t in todo:
            run.execute(t)
    except KeyboardInterrupt:
        status = "interrupted"
        for t in todo:
            if (t.step, t.unit) not in run.records:
                run.settle(t, "not-run", "run cancelled")
    if status != "interrupted":
        bad = any(r["status"] in ("failed", "not-run") for r in run.records.values())
        status = "failed" if bad else "ok"
    run_doc.update(status=status, ended=_now(), exit_code=0 if status == "ok" else 1)
    write_json_atomic(os.path.join(run_dir, "run.json"), run_doc)
    return RunResult(run_id, run_dir, status, plan, run.records)
