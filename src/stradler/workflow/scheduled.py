"""Runs on a scheduler add-on: jobs, finalize job, chain entry point and fallback reader (ADDONS.md 2 to 5)."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ..addons import Addon, AddonError, check_compatibility
from .areas import AreaIndex
from .fs import LocalFS
from .identity import task_record_dir
from .plan import Plan, PlanError, TaskPlan

__all__ = ["MANIFEST", "cancel_run", "collect_run", "finalize_run", "prepare", "run_chain", "submit_run"]

MANIFEST = "chains.json"
CANCEL_MARKER = ".cancelled"
TERMINAL = {"completed", "failed", "cancelled", "lost"}
SUBMIT_TRIES = 3
_NAME_BAD = re.compile(r"[^A-Za-z0-9_-]")
_MEM = {"K": 1, "M": 1024, "G": 1024 ** 2, "T": 1024 ** 3}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write(path: str, doc: Any) -> None:
    from .execute import write_json_atomic
    write_json_atomic(path, doc)


def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _seconds(t: str) -> int:
    days, _, rest = t.rpartition("-")
    h, m, s = (int(x) for x in rest.split(":"))
    return (int(days) if days else 0) * 86400 + h * 3600 + m * 60 + s


def _mem(v: str) -> float:
    return float(v[:-1]) * _MEM[v[-1].upper()]


def _resources(recipe: Any, steps: Sequence[str]) -> dict[str, Any]:
    """Maximum of each resource over the steps of a segment (section 2.3)."""
    out: dict[str, Any] = {}
    for sn in steps:
        for k, v in recipe.step(sn).resources.items():
            if k not in out:
                out[k] = v
            elif k == "memory":
                out[k] = v if _mem(str(v)) > _mem(str(out[k])) else out[k]
            elif k == "time":
                out[k] = v if _seconds(str(v)) > _seconds(str(out[k])) else out[k]
            else:
                out[k] = max(out[k], v)
    return out


def _name(prefix: str, family: str, steps: Sequence[str], unit: str | None) -> str:
    parts = [prefix, family, "-".join(steps)] + ([unit] if unit else [])
    return _NAME_BAD.sub("_", "-".join(parts))


@dataclasses.dataclass
class Prepared:
    """What ``prepare`` computed: the jobs of the run and the add-on answers, before anything is written."""

    scheduler: Addon
    caps: dict[str, Any]
    segments: list[dict[str, Any]]
    wrapped: dict[str, dict[str, Any]]
    runtimes: dict[str, Addon]
    recipe_file: str
    final_name: str = ""


def _binds(root: str, areas: AreaIndex) -> list[dict[str, str]]:
    out = []
    for a in areas.areas.values():
        src = os.path.join(root, a["path"].strip("/"))
        ro = a.get("role") == "input" or a.get("mutability") == "sealed"
        out.append({"source": src, "target": src, "mode": "ro" if ro else "rw"})
    return out


def prepare(recipe: Any, root: str, areas: AreaIndex, plan: Plan, addons: Mapping[str, Addon], scheduler: str,
            runtime: str | None, job_prefix: str, recipe_file: str | None) -> Prepared:
    """Check the pairing, name the jobs and wrap container tasks. Writes nothing.

    Raises
    ------
    PlanError
        When the recipe was built in memory (a job process reads the recipe file) or an add-on is missing.
    PlanRefused
        For an incompatible pairing (section 5).
    """
    from ..addons import PlanRefused
    if not recipe_file:
        raise PlanError("a run on a scheduler add-on needs recipe_file: the job process reads the recipe from it")
    sched = addons.get(scheduler)
    if sched is None or sched.kind != "scheduler":
        raise PlanRefused("incompatible.missing_add_on", f'stradler: plan refused: incompatible.missing_add_on: '
                          f'scheduler "{scheduler}" has no [addons] entry of kind "scheduler"; plan: needs one.')
    runtimes = {n: a for n, a in addons.items() if a.kind == "container_runtime" and (runtime in (None, n))}
    if runtime is not None and runtime not in runtimes:
        raise PlanRefused("incompatible.missing_add_on", f'stradler: plan refused: incompatible.missing_add_on: '
                          f'container_runtime "{runtime}" has no [addons] entry; plan: needs one.')
    segs: list[dict[str, Any]] = []
    for i, s in enumerate(plan.segments):
        chains = [[t for t in c.tasks if t.status == "planned"] for c in s.chains]
        chains = [c for c in chains if c]
        if not chains:
            continue
        unit = chains[0][0].unit if len(chains) == 1 else None
        segs.append({"index": i, "steps": list(s.steps), "chains": chains,
                     "name": _name(job_prefix, recipe.family, s.steps, unit),
                     "resources": _resources(recipe, s.steps)})
    final_name = _name(job_prefix, recipe.family, ["finalize"], None)
    names = [s["name"] for s in segs] + [final_name]
    binds = _binds(root, areas)
    found = check_compatibility(plan, recipe, scheduler=sched, runtimes=runtimes, root=root, names=names, binds=binds)
    wrapped: dict[str, dict[str, Any]] = {}
    for sg in segs:
        for ch in sg["chains"]:
            for t in ch:
                cname = recipe.step(t.step).container
                if cname == "none":
                    continue
                rt = runtimes[found["runtime_of"][cname]]
                c = recipe.containers[cname]
                info = found["images"][cname]
                image = os.path.join(root, c.pin.path or c.image)
                argv = rt.call("wrap", {"argv": list(t.argv or []), "image": image, "binds": binds,
                                        "workdir": None})["argv"]
                obj: dict[str, Any] = {"name": cname, "kind": c.kind, "pin_digest": "sha256:" + c.pin.sha256,
                                       "resolved_digest": info["digest"], "format": info["format"]}
                if c.pin.placeholder:
                    obj["pin_placeholder"] = True
                else:
                    obj["pin_matches"] = info["digest"] == obj["pin_digest"]
                wrapped[f"{t.step}\t{t.unit}"] = {"argv": argv, "container": obj}
    return Prepared(sched, found["scheduler"], segs, wrapped, runtimes, recipe_file, final_name)


def _submit(addon: Addon, key: str, jobs: list[dict[str, Any]], log: Any) -> list[dict[str, Any]]:
    err: AddonError | None = None
    for _ in range(SUBMIT_TRIES):
        try:
            return addon.call("submit", {"submission_key": key, "jobs": jobs})["jobs"]
        except AddonError as exc:
            log(f"submit {key}: {exc}")
            err = exc
            if exc.code != "internal":
                break
    assert err is not None
    raise err


def submit_run(prep: Prepared, root: str, run_dir: str, run_doc: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Write the job manifest, submit one job per segment and then the finalize job (ADDONS 2.7, 3.2).

    Returns
    -------
    dict
        The ``run.json`` document as written; ``status`` stays ``running`` on a non-blocking scheduler.
    """
    sched, caps = prep.scheduler, prep.caps
    log_dir = os.path.join(run_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    def log(msg: str) -> None:
        with open(os.path.join(log_dir, "submit.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{_now()} {msg}\n")

    manifest = {
        "root": root, "recipe_file": prep.recipe_file,
        "segments": [{"index": s["index"], "chains": [[dataclasses.asdict(t) for t in c] for c in s["chains"]]}
                     for s in prep.segments],
        "wrapped": prep.wrapped,
    }
    _write(os.path.join(run_dir, MANIFEST), manifest)
    run_doc.update(backend=sched.name, backend_version=sched.version)
    _write(os.path.join(run_dir, "run.json"), run_doc)
    ids: list[str] = []
    try:
        for n, s in enumerate(prep.segments):
            array = None if len(s["chains"]) == 1 else len(s["chains"])
            job = {"name": s["name"], "argv": [sys.executable, "-m", "stradler", "workflow", "chain", run_dir,
                                               str(n)],
                   "cwd": root, "array": array, "resources": s["resources"], "log_dir": log_dir,
                   "dependencies": [{"on": "end", "jobs": [ids[-1]]}] if ids else []}
            r = _submit(sched, f"{run_id}-s{n}", [job], log)[0]
            ids.append(r["job_id"])
            seg = run_doc["segments"][s["index"]]
            seg.update(job_name=r["name"], job_id=r["job_id"], array_size=r.get("array_size"))
            _write(os.path.join(run_dir, "run.json"), run_doc)
    finally:
        sched.flush_stderr(log_dir)
    res = {"cpus": 1} if "cpus" in caps.get("resources", []) else {}
    fin = {"name": prep.final_name,
           "argv": [sys.executable, "-m", "stradler", "workflow", "finalize", run_dir], "cwd": root, "array": None,
           "resources": res, "log_dir": log_dir, "dependencies": [{"on": "end", "jobs": list(ids)}]}
    if ids and caps.get("non_blocking"):
        try:
            _submit(sched, f"{run_id}-finalize", [fin], log)
        except AddonError as exc:  # section 3.7: the run stays "running"; the fallback reader applies
            log(f"finalize job not submitted: {exc}")
        finally:
            sched.flush_stderr(log_dir)
    if not caps.get("non_blocking"):
        _wait(sched, ids)
        finalize_run(run_dir)
    elif not ids:
        finalize_run(run_dir)
    return _read(os.path.join(run_dir, "run.json"))


def _wait(sched: Addon, ids: list[str], every: float = 0.2) -> None:
    while ids:
        states = sched.call("status", {"job_ids": ids})["states"]
        if all(s["state"] in TERMINAL for s in states):
            return
        time.sleep(every)


def _records(run_dir: str, plan: Mapping[str, Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, str]]]:
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    for s in plan["segments"]:
        for c in s["chains"]:
            for t in c["tasks"]:
                p = os.path.join(task_record_dir(run_dir, t["step"], t["unit"]), "task.json")
                if os.path.isfile(p):
                    recs[(t["step"], t["unit"])] = _read(p)
                else:
                    missing.append({"step": t["step"], "unit": t["unit"]})
    return recs, missing


def read_records(run_dir: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Return the ``task.json`` records of a run by ``(step, unit)``."""
    return _records(run_dir, _read(os.path.join(run_dir, "plan.json")))[0]


def finalize_run(run_dir: str, job_id: str | None = None) -> dict[str, Any]:
    """Write the final status of a run (ADDONS 3.3); idempotent.

    Reads every ``task.json`` of the plan, derives the run status, and rewrites ``run.json`` only while its status
    is ``running``. Writes no task record. A planned task without a record counts as failed and is listed in
    ``missing_tasks``.

    Parameters
    ----------
    run_dir : str
    job_id : str, optional
        Id of the finalize job; default the environment variable ``STRADLER_JOB_ID``.

    Returns
    -------
    dict
        The ``run.json`` document after the call.
    """
    path = os.path.join(run_dir, "run.json")
    doc = _read(path)
    if doc.get("status") != "running":
        return doc
    recs, missing = _records(run_dir, _read(os.path.join(run_dir, "plan.json")))
    if os.path.exists(os.path.join(run_dir, CANCEL_MARKER)):
        status = "interrupted"
    elif missing or any(r["status"] in ("failed", "not-run") for r in recs.values()):
        status = "failed"
    else:
        status = "ok"
    ends = [r["ended"] for r in recs.values() if r.get("ended")]
    doc.update(status=status, ended=max(ends) if ends else _now(), exit_code=0 if status == "ok" else 1)
    job_id = job_id or os.environ.get("STRADLER_JOB_ID")
    if job_id:
        doc["finalize_job_id"] = job_id
    if missing:
        doc["missing_tasks"] = missing
    _write(path, doc)
    return doc


def collect_run(run_dir: str, scheduler: Addon) -> dict[str, Any]:
    """Fallback reader (ADDONS 3.6): finalize a ``running`` run whose jobs have all ended, by job status."""
    doc = _read(os.path.join(run_dir, "run.json"))
    if doc.get("status") != "running":
        return doc
    ids = [s["job_id"] for s in doc["segments"] if s.get("job_id")]
    if ids and all(s["state"] in TERMINAL for s in scheduler.call("status", {"job_ids": ids})["states"]):
        return finalize_run(run_dir, job_id=doc.get("finalize_job_id"))
    return doc


def cancel_run(run_dir: str, scheduler: Addon) -> None:
    """Write the cancel marker, then cancel the run's jobs (ADDONS 3.5)."""
    doc = _read(os.path.join(run_dir, "run.json"))
    open(os.path.join(run_dir, CANCEL_MARKER), "w", encoding="utf-8").close()
    ids = [s["job_id"] for s in doc["segments"] if s.get("job_id")]
    if ids:
        scheduler.call("cancel", {"job_ids": ids})


def run_chain(run_dir: str, segment: int, index: int | None = None) -> int:
    """Job body: run the tasks of one chain in order and stop at the first failure (WORKFLOWS 5.2).

    Parameters
    ----------
    run_dir : str
    segment : int
        Position in the manifest's segment list (the job's position).
    index : int, optional
        Chain index; default the environment variable ``STRADLER_TASK_INDEX``, else 0.

    Returns
    -------
    int
        0 when every task of the chain is ok, else 1.
    """
    from .execute import _Run
    from .schema import load_recipe
    man = _read(os.path.join(run_dir, MANIFEST))
    root = man["root"]
    if index is None:
        index = int(os.environ.get("STRADLER_TASK_INDEX", "0"))
    chain = [TaskPlan(**d) for d in man["segments"][segment]["chains"][index]]
    recipe = load_recipe(os.path.join(root, man["recipe_file"]))
    run = _Run(recipe, root, run_dir, None, LocalFS(root))
    run.wrapped = man["wrapped"]
    plan = _read(os.path.join(run_dir, "plan.json"))
    for rec in _records(run_dir, plan)[0].values():
        if rec["status"] in ("failed", "not-run"):
            run.dead.update((rec.get("outputs") or {}).values())
    ok = True
    for t in chain:
        if not ok:
            run.settle(t, "not-run", "earlier task of the chain did not end ok")
            continue
        run.execute(t)
        ok = run.records[(t.step, t.unit)]["status"] == "ok"
    return 0 if ok else 1
