"""Fake scheduler add-on: one JSON request on stdin, one JSON response on stdout (ADDONS.md section 1).

It queues jobs in a JSON state file named by ``settings.state`` and runs them only on the extra operation
``drain`` (a test hook), so a test sees a non-blocking scheduler. ``settings.caps`` overrides capabilities;
``settings.lose`` lists job names that are never run (state ``lost``).
"""

import json
import os
import subprocess
import sys

DEFAULT_CAPS = {
    "kind": "scheduler", "version": "0.1.0", "non_blocking": True, "dependency_kinds": ["success", "end"],
    "array_jobs": True, "resources": ["cpus", "memory", "time", "gpus"], "platforms": ["any"],
    "container_image_formats": ["sif"], "job_name_max": 64,
    "states": ["pending", "running", "completed", "failed", "cancelled", "lost"],
}


def reply(ok, result=None, error=None, code=0):
    doc = {"protocol_version": "1.0", "ok": ok}
    doc.update({"result": result} if ok else {"error": error})
    print(json.dumps(doc))
    sys.exit(code)


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"next": 1, "jobs": {}, "requests": []}


def save(path, state):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def run_job(job_id, job):
    codes = []
    for i in range(job["array"] or 1):
        env = dict(os.environ, STRADLER_JOB_ID=job_id, STRADLER_TASK_INDEX=str(i))
        codes.append(subprocess.run(job["argv"], cwd=job["cwd"], env=env, capture_output=True,
                                    check=False).returncode)
    return codes


def main():
    try:
        req = json.loads(sys.stdin.read())
    except ValueError:
        reply(False, error={"code": "invalid_request", "message": "bad json"}, code=2)
    if req.get("protocol_version", "").split(".")[0] != "1":
        reply(False, error={"code": "unsupported_version", "message": "need 1.x"}, code=3)
    settings, op = req["settings"], req["op"]
    path = settings["state"]
    state = load(path)
    state["requests"].append(req)
    caps = dict(DEFAULT_CAPS, **settings.get("caps", {}))
    if op == "capabilities":
        save(path, state)
        reply(True, caps)
    if op == "submit":
        out = []
        for job in req["params"]["jobs"]:
            for dep in job["dependencies"]:
                if dep["on"] not in caps["dependency_kinds"] or any(j not in state["jobs"] for j in dep["jobs"]):
                    reply(False, error={"code": "refused", "message": "bad dependency"}, code=1)
            job_id = f"j{state['next']}"
            state["next"] += 1
            state["jobs"][job_id] = dict(job, state="pending", codes=None)
            out.append({"name": job["name"], "job_id": job_id, "array_size": job["array"]})
            if not caps["non_blocking"]:  # a blocking scheduler returns after the job ended
                codes = run_job(job_id, job)
                state["jobs"][job_id].update(codes=codes, state="completed" if not any(codes) else "failed")
        save(path, state)
        reply(True, {"jobs": out})
    if op == "drain":
        for job_id, job in state["jobs"].items():
            if job["state"] != "pending":
                continue
            if job["name"] in settings.get("lose", []):
                job["state"] = "lost"
                continue
            job["codes"] = run_job(job_id, job)
            job["state"] = "completed" if not any(job["codes"]) else "failed"
        save(path, state)
        reply(True, {})
    if op in ("status", "cancel"):
        ids = req["params"]["job_ids"]
        if op == "cancel":
            for j in ids:
                if state["jobs"][j]["state"] == "pending":
                    state["jobs"][j]["state"] = "cancelled"
            save(path, state)
        states = [{"job_id": j, "task": None, "state": state["jobs"][j]["state"], "exit_code": None, "reason": None}
                  for j in ids]
        reply(True, {"states": states})
    save(path, state)
    reply(False, error={"code": "unsupported_operation", "message": op}, code=1)


main()
