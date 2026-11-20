"""Slurm scheduler add-on (ADDONS 2, 3): one JSON request on stdin, one JSON response on stdout.

Standard library only. This module must not import the stradler core planner or executor: the protocol is the whole
interface, so the add-on can move to its own package.

The native commands are injectable: environment variables STRADLER_SLURM_SBATCH, _SQUEUE, _SACCT, _SCANCEL name the
executables (default: the plain command names), or a ``Commands`` object is passed to ``handle``/``main``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

PROTOCOL_VERSION = "1.0"
ADDON_VERSION = "0.1.0"
JOB_NAME_MAX = 128
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ID_RE = re.compile(r"^\d+(_(\d+|\[[\d,\-]+\]))?$")
MEM_RE = re.compile(r"^\d+(\.\d+)?[KMGT]$")
TIME_RE = re.compile(r"^(\d+-)?\d{1,2}:\d{2}:\d{2}$")
STATES = ["pending", "running", "completed", "failed", "cancelled", "lost"]

# Native Slurm state -> protocol state (ADDONS 2.5; docs/addons/slurm.md "State mapping" table).
STATE_MAP = {
    "PENDING": "pending", "CONFIGURING": "pending", "REQUEUED": "pending", "REQUEUE_HOLD": "pending",
    "REQUEUE_FED": "pending", "RESV_DEL_HOLD": "pending", "SPECIAL_EXIT": "pending",
    "RUNNING": "running", "COMPLETING": "running", "SIGNALING": "running", "STAGE_OUT": "running",
    "RESIZING": "running", "SUSPENDED": "running", "STOPPED": "running", "UPDATE_DB": "running",
    "COMPLETED": "completed",
    "FAILED": "failed", "TIMEOUT": "failed", "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed",
    "BOOT_FAIL": "failed", "DEADLINE": "failed", "PREEMPTED": "failed",
    "CANCELLED": "cancelled", "REVOKED": "cancelled",
}
ERROR_CODES = {"invalid_request", "unsupported_version", "unsupported_operation", "refused", "not_found",
               "backend_error", "internal"}
EXIT_FOR = {"invalid_request": 2, "unsupported_version": 3, "internal": 4}


class AddonError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        assert code in ERROR_CODES
        self.code, self.message, self.details = code, message, details or {}


@dataclass
class Commands:
    sbatch: str = "sbatch"
    squeue: str = "squeue"
    sacct: str = "sacct"
    scancel: str = "scancel"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(*(env.get(f"STRADLER_SLURM_{n.upper()}", n) for n in ("sbatch", "squeue", "sacct", "scancel")))


def run(argv, stdin=None):
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise AddonError("backend_error", f"cannot run {argv[0]}: {exc}", {"argv": argv}) from exc
    return p.returncode, p.stdout, p.stderr


def _only(params, allowed):
    extra = sorted(set(params) - allowed)
    if extra:
        raise AddonError("invalid_request", f"unknown params keys: {', '.join(extra)}", {"keys": extra})


# ---------------------------------------------------------------- capabilities
def op_capabilities(params, settings, cmds):
    _only(params, set())
    return {"kind": "scheduler", "version": ADDON_VERSION, "non_blocking": True,
            "dependency_kinds": ["success", "end"], "array_jobs": True,
            "resources": ["cpus", "memory", "time", "gpus"], "platforms": ["linux"],
            "container_image_formats": ["sif"], "job_name_max": JOB_NAME_MAX, "states": list(STATES)}


# ---------------------------------------------------------------- submit
def _comment(key, name):
    return f"stradler:{key}:{name}"


def _check_job(job):
    if not isinstance(job, dict):
        raise AddonError("invalid_request", "each job must be an object")
    _only(job, {"name", "argv", "cwd", "array", "resources", "log_dir", "dependencies"})
    for k in ("name", "argv", "cwd", "log_dir"):
        if k not in job:
            raise AddonError("invalid_request", f"job lacks '{k}'")
    name = job["name"]
    if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > JOB_NAME_MAX:
        raise AddonError("invalid_request", f"bad job name: {name!r}")
    argv = job["argv"]
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
        raise AddonError("invalid_request", "argv must be a non-empty list of strings")
    for k in ("cwd", "log_dir"):
        if not (isinstance(job[k], str) and job[k].startswith("/")):
            raise AddonError("invalid_request", f"{k} must be an absolute path")
    arr = job.get("array")
    if arr is not None and not (isinstance(arr, int) and not isinstance(arr, bool) and arr >= 1):
        raise AddonError("invalid_request", "array must be null or an integer >= 1")
    res = job.get("resources") or {}
    _only(res, {"cpus", "memory", "time", "gpus"})
    if "cpus" in res and not (isinstance(res["cpus"], int) and res["cpus"] >= 1):
        raise AddonError("invalid_request", "cpus must be an integer >= 1")
    if "gpus" in res and not (isinstance(res["gpus"], int) and res["gpus"] >= 0):
        raise AddonError("invalid_request", "gpus must be an integer >= 0")
    if "memory" in res and not MEM_RE.match(str(res["memory"])):
        raise AddonError("invalid_request", f"bad memory: {res['memory']!r}")
    if "time" in res and not TIME_RE.match(str(res["time"])):
        raise AddonError("invalid_request", f"bad time: {res['time']!r}")
    for dep in job.get("dependencies") or []:
        _only(dep, {"on", "jobs"})
        if dep.get("on") not in ("success", "end"):
            raise AddonError("invalid_request", f"bad dependency kind: {dep.get('on')!r}")
        for jid in dep.get("jobs", []):
            if not (isinstance(jid, str) and ID_RE.match(jid)):
                raise AddonError("invalid_request", f"bad job id: {jid!r}")


def sbatch_argv(job, settings, cmds, key):
    """Argument vector of one sbatch call (the script itself goes to stdin)."""
    arr = job.get("array")
    res = job.get("resources") or {}
    stem = f"{job['log_dir'].rstrip('/')}/{job['name']}" + ("-%a" if arr else "")
    a = [cmds.sbatch, "--parsable", f"--job-name={job['name']}", f"--chdir={job['cwd']}",
         f"--output={stem}.out", f"--error={stem}.err", f"--comment={_comment(key, job['name'])}",
         "--export=ALL"]
    if arr:
        a.append(f"--array=0-{arr - 1}")
    for dep in job.get("dependencies") or []:
        if dep["jobs"]:
            kind = "afterany" if dep["on"] == "end" else "afterok"
            a.append(f"--dependency={kind}:" + ":".join(dep["jobs"]))
    if "cpus" in res:
        a.append(f"--cpus-per-task={res['cpus']}")
    if "memory" in res:
        a.append(f"--mem={res['memory']}")
    if "time" in res:
        a.append(f"--time={res['time']}")
    if res.get("gpus"):
        a.append(f"--gres=gpu:{res['gpus']}")
    for k, flag in (("partition", "--partition"), ("account", "--account"), ("qos", "--qos")):
        if settings.get(k):
            a.append(f"{flag}={settings[k]}")
    return a


def script_for(job):
    lines = ["#!/bin/sh",
             'STRADLER_JOB_ID="${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}"; export STRADLER_JOB_ID']
    if job.get("array"):
        lines.append('STRADLER_TASK_INDEX="$SLURM_ARRAY_TASK_ID"; export STRADLER_TASK_INDEX')
    lines.append("exec " + " ".join(shlex.quote(x) for x in job["argv"]))
    return "\n".join(lines) + "\n"


def _find_submitted(key, names, cmds):
    """Job ids already submitted under this key (job comment lookup; squeue, then sacct)."""
    prefix = f"stradler:{key}:"
    rows = []
    rc, out, _ = run([cmds.squeue, "--noheader", "-o", "%i|%k"])
    rows += out.splitlines() if rc == 0 else []
    rc, out, _ = run([cmds.sacct, "-X", "-n", "-P", "--format=JobID,Comment"])
    rows += out.splitlines() if rc == 0 else []
    found = {}
    for row in rows:
        jid, _, com = row.partition("|")
        if com.startswith(prefix) and "_" not in jid:
            found.setdefault(com[len(prefix):], jid.strip())
    return found if all(n in found for n in names) else None


def op_submit(params, settings, cmds):
    _only(params, {"jobs", "submission_key"})
    key = params.get("submission_key")
    if not (isinstance(key, str) and NAME_RE.match(key)):
        raise AddonError("invalid_request", "submission_key must match [A-Za-z0-9_-]+")
    jobs = params.get("jobs")
    if not isinstance(jobs, list):
        raise AddonError("invalid_request", "jobs must be a list")
    for job in jobs:
        _check_job(job)
    earlier = _find_submitted(key, [j["name"] for j in jobs], cmds) if jobs else None
    if earlier:
        return {"jobs": [{"name": j["name"], "job_id": earlier[j["name"]], "array_size": j.get("array")}
                         for j in jobs]}
    done, result = [], []
    for job in jobs:
        rc, out, err = run(sbatch_argv(job, settings, cmds, key), stdin=script_for(job))
        jid = out.strip().split(";")[0].strip()
        if rc != 0 or not re.match(r"^\d+$", jid):
            if done:
                run([cmds.scancel] + done)
            raise AddonError("backend_error", f"sbatch failed for job {job['name']}",
                             {"exit_code": rc, "stderr": err.strip(), "cancelled": done})
        done.append(jid)
        result.append({"name": job["name"], "job_id": jid, "array_size": job.get("array")})
    return {"jobs": result}


# ---------------------------------------------------------------- status / cancel
def _split_id(raw):
    """'123' -> ('123', [None]); '123_4' -> ('123', [4]); '123_[0-2,5]' -> ('123', [0,1,2,5])."""
    base, sep, rest = raw.strip().partition("_")
    if not sep:
        return base, [None]
    if rest.startswith("["):
        tasks = []
        for part in rest.strip("[]").split("%")[0].split(","):
            lo, _, hi = part.partition("-")
            tasks += range(int(lo), int(hi or lo) + 1)
        return base, tasks
    return base, [int(rest)]


def _map_state(native):
    parts = native.split()
    word = parts[0].rstrip("+") if parts else ""
    if word not in STATE_MAP:
        raise AddonError("backend_error", f"unmapped Slurm state: {native!r}", {"state": native})
    return STATE_MAP[word]


def _exit(text):
    first, _, sig = (text or "").partition(":")
    try:
        code = int(first)
    except ValueError:
        return None
    if code == 0 and sig.isdigit() and int(sig) > 0:
        return 128 + int(sig)
    return code


def _row(base, task, native, exit_text, reason):
    state = _map_state(native)
    if state == "pending" and reason == "DependencyNeverSatisfied":
        state = "cancelled"
    code = _exit(exit_text) if state in ("completed", "failed") else None
    if state == "failed" and code == 0:
        code = 1
    return {"job_id": base, "task": task, "state": state, "exit_code": code, "reason": reason or None}


def _ids(params):
    ids = params.get("job_ids")
    if not (isinstance(ids, list) and all(isinstance(i, str) and ID_RE.match(i) for i in ids)):
        raise AddonError("invalid_request", "job_ids must be a list of job id strings")
    return ids


def query_states(ids, cmds):
    if not ids:
        return []
    joined = ",".join(ids)
    seen = {}
    rc, out, _ = run([cmds.squeue, "--noheader", "-r", "-o", "%i|%T|%r", "-j", joined])
    if rc == 0:
        for line in out.splitlines():
            if line.strip():
                jid, state, reason = (line.split("|") + ["", ""])[:3]
                base, tasks = _split_id(jid)
                for t in tasks:
                    seen[(base, t)] = _row(base, t, state, None, None if reason in ("None", "") else reason)
    rc, out, _ = run([cmds.sacct, "-X", "-n", "-P", "--format=JobID,State,ExitCode", "-j", joined])
    if rc == 0:
        for line in out.splitlines():
            if line.strip():
                jid, state, code = (line.split("|") + ["", ""])[:3]
                base, tasks = _split_id(jid)
                for t in tasks:
                    seen.setdefault((base, t), _row(base, t, state, code, None))
    rows = []
    for req in ids:
        rbase, rtasks = _split_id(req)
        keys = sorted((k for k in seen if k[0] == rbase and (rtasks == [None] or k[1] in rtasks)),
                      key=lambda k: -1 if k[1] is None else k[1])
        if keys:
            rows += [seen[k] for k in keys]
        else:
            rows.append({"job_id": rbase, "task": None if rtasks == [None] else rtasks[0],
                         "state": "lost", "exit_code": None, "reason": None})
    return rows


def op_status(params, settings, cmds):
    _only(params, {"job_ids"})
    return {"states": query_states(_ids(params), cmds)}


def op_cancel(params, settings, cmds):
    _only(params, {"job_ids"})
    ids = _ids(params)
    if ids:
        rc, _, err = run([cmds.scancel] + ids)
        if rc != 0 and "Invalid job id" not in err:
            raise AddonError("backend_error", "scancel failed", {"exit_code": rc, "stderr": err.strip()})
    return {"states": query_states(ids, cmds)}


OPS = {"capabilities": op_capabilities, "submit": op_submit, "status": op_status, "cancel": op_cancel}


# ---------------------------------------------------------------- protocol entry
def handle(text, cmds=None):
    """Return (response dict, exit code) for one request text."""
    cmds = cmds or Commands.from_env()
    try:
        try:
            req = json.loads(text)
        except ValueError as exc:
            raise AddonError("invalid_request", f"request is not valid JSON: {exc}") from exc
        if not isinstance(req, dict):
            raise AddonError("invalid_request", "request must be a JSON object")
        ver = req.get("protocol_version")
        if not isinstance(ver, str) or not re.match(r"^\d+\.\d+$", ver):
            raise AddonError("invalid_request", "protocol_version missing or malformed")
        if ver.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
            raise AddonError("unsupported_version", f"protocol {ver} not supported (speaks {PROTOCOL_VERSION})")
        if req.get("kind") != "scheduler":
            raise AddonError("invalid_request", "kind must be 'scheduler'")
        op = req.get("op")
        settings, params = req.get("settings", {}), req.get("params", {})
        if not isinstance(settings, dict) or not isinstance(params, dict):
            raise AddonError("invalid_request", "settings and params must be objects")
        if op not in OPS:
            raise AddonError("unsupported_operation", f"unknown operation: {op!r}")
        return {"protocol_version": PROTOCOL_VERSION, "ok": True, "result": OPS[op](params, settings, cmds)}, 0
    except AddonError as e:
        return ({"protocol_version": PROTOCOL_VERSION, "ok": False,
                 "error": {"code": e.code, "message": e.message, "details": e.details}}, EXIT_FOR.get(e.code, 1))
    except Exception as e:  # noqa: BLE001 - internal failure envelope
        return ({"protocol_version": PROTOCOL_VERSION, "ok": False,
                 "error": {"code": "internal", "message": f"{type(e).__name__}: {e}", "details": {}}}, 4)


def main(argv=None, stdin=None, stdout=None, cmds=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    resp, code = handle(stdin.read(), cmds)
    stdout.write(json.dumps(resp) + "\n")
    stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
