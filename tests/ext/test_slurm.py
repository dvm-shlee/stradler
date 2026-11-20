"""Slurm add-on tests: fake sbatch/squeue/sacct/scancel executables; real Slurm is never called."""
import io
import json
import os
import stat
import subprocess
import sys

import pytest

from stradler.ext import slurm


def _fake(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def fake(tmp_path):
    """Fake commands; canned outputs are files in tmp_path: <cmd>.out, <cmd>.rc, <cmd>.err."""
    d = tmp_path / "bin"
    d.mkdir()
    log = tmp_path / "calls.log"
    script = (
        f'printf "%s\\n" "$0 $*" >> {log}\n'
        f'[ -n "$FAKE_STDIN" ] && cat > {tmp_path}/$(basename $0).stdin\n'
        f'[ -f {tmp_path}/$(basename $0).err ] && cat {tmp_path}/$(basename $0).err >&2\n'
        f'[ -f {tmp_path}/$(basename $0).out ] && cat {tmp_path}/$(basename $0).out\n'
        f'if [ -f {tmp_path}/$(basename $0).rc ]; then exit $(cat {tmp_path}/$(basename $0).rc); fi\n'
        'exit 0\n')
    names = {n: _fake(d / n, script) for n in ("sbatch", "squeue", "sacct", "scancel")}
    cmds = slurm.Commands(**names)

    class F:
        pass

    f = F()
    f.cmds, f.dir, f.log = cmds, tmp_path, log
    f.set = lambda cmd, out="", rc=0, err="": (
        (tmp_path / f"{cmd}.out").write_text(out), (tmp_path / f"{cmd}.rc").write_text(str(rc)),
        (tmp_path / f"{cmd}.err").write_text(err))
    f.calls = lambda cmd: [ln.split(" ", 1)[1].split(" ") for ln in log.read_text().splitlines()
                           if os.path.basename(ln.split(" ", 1)[0]) == cmd] if log.exists() else []
    return f


def req(op, params=None, settings=None, version="1.0", kind="scheduler"):
    return json.dumps({"protocol_version": version, "kind": kind, "op": op, "settings": settings or {},
                       "params": params or {}})


JOB = {"name": "demo-step", "argv": ["echo", "a b"], "cwd": "/w", "array": None, "log_dir": "/w/logs",
       "resources": {}, "dependencies": []}


def test_capabilities(fake):
    resp, code = slurm.handle(req("capabilities"), fake.cmds)
    assert code == 0 and resp["ok"]
    r = resp["result"]
    assert r["kind"] == "scheduler" and r["non_blocking"] is True
    assert r["dependency_kinds"] == ["success", "end"] and r["array_jobs"] is True
    assert r["states"] == ["pending", "running", "completed", "failed", "cancelled", "lost"]


def test_submit_array_dependency_resources(fake, monkeypatch):
    monkeypatch.setenv("FAKE_STDIN", "1")
    fake.set("sbatch", "4242\n")
    job = dict(JOB, array=3, resources={"cpus": 4, "memory": "8G", "time": "1-02:00:00", "gpus": 2},
               dependencies=[{"on": "end", "jobs": ["10", "11"]}, {"on": "success", "jobs": ["12"]}])
    resp, code = slurm.handle(req("submit", {"jobs": [job], "submission_key": "k1"},
                                  {"partition": "p", "account": "a"}), fake.cmds)
    assert code == 0, resp
    assert resp["result"]["jobs"] == [{"name": "demo-step", "job_id": "4242", "array_size": 3}]
    argv = fake.calls("sbatch")[0]
    for want in ("--parsable", "--job-name=demo-step", "--chdir=/w", "--array=0-2",
                 "--output=/w/logs/demo-step-%a.out", "--error=/w/logs/demo-step-%a.err",
                 "--dependency=afterany:10:11", "--dependency=afterok:12", "--cpus-per-task=4", "--mem=8G",
                 "--time=1-02:00:00", "--gres=gpu:2", "--partition=p", "--account=a",
                 "--comment=stradler:k1:demo-step"):
        assert want in argv, want
    body = (fake.dir / "sbatch.stdin").read_text()
    assert "STRADLER_TASK_INDEX" in body and "exec echo 'a b'" in body


def test_submit_single_job_no_array(fake):
    fake.set("sbatch", "7;cluster\n")
    resp, _ = slurm.handle(req("submit", {"jobs": [JOB], "submission_key": "k"}), fake.cmds)
    assert resp["result"]["jobs"][0] == {"name": "demo-step", "job_id": "7", "array_size": None}
    argv = fake.calls("sbatch")[0]
    assert not any(a.startswith(("--array", "--dependency")) for a in argv)
    assert "--output=/w/logs/demo-step.out" in argv


def test_submit_idempotent_by_key(fake):
    fake.set("squeue", "99|stradler:kk:demo-step\n")
    resp, code = slurm.handle(req("submit", {"jobs": [JOB], "submission_key": "kk"}), fake.cmds)
    assert code == 0 and resp["result"]["jobs"][0]["job_id"] == "99"
    assert fake.calls("sbatch") == []


def test_submit_sbatch_failure_envelope_and_rollback(fake, tmp_path):
    # first sbatch succeeds, second fails: first is cancelled, error envelope returned
    counter = tmp_path / "n"
    _fake(tmp_path / "bin" / "sbatch",
          f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n'
          'cat >/dev/null\nif [ $n -ge 2 ]; then echo "bad partition" >&2; exit 1; fi\necho 500\n')
    j2 = dict(JOB, name="second")
    resp, code = slurm.handle(req("submit", {"jobs": [JOB, j2], "submission_key": "kx"}), fake.cmds)
    assert code == 1 and resp["ok"] is False
    assert resp["error"]["code"] == "backend_error"
    assert "bad partition" in resp["error"]["details"]["stderr"]
    assert fake.calls("scancel") == [["500"]]


def test_submit_rejects_unknown_param_and_bad_name(fake):
    resp, code = slurm.handle(req("submit", {"jobs": [], "submission_key": "k", "extra": 1}), fake.cmds)
    assert code == 2 and resp["error"]["code"] == "invalid_request"
    resp, code = slurm.handle(req("submit", {"jobs": [dict(JOB, name="bad name")], "submission_key": "k"}),
                              fake.cmds)
    assert code == 2 and resp["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("native,state", [
    ("PENDING", "pending"), ("CONFIGURING", "pending"), ("RUNNING", "running"), ("COMPLETING", "running"),
    ("COMPLETED", "completed"), ("FAILED", "failed"), ("TIMEOUT", "failed"), ("OUT_OF_MEMORY", "failed"),
    ("NODE_FAIL", "failed"), ("CANCELLED", "cancelled"), ("CANCELLED by 1234", "cancelled"),
    ("CANCELLED+", "cancelled")])
def test_state_mapping_table(native, state):
    assert slurm._row("1", None, native, "0:0", None)["state"] == state


def test_unmapped_state_is_backend_error(fake):
    fake.set("squeue", "5|WEIRDSTATE|None\n")
    resp, code = slurm.handle(req("status", {"job_ids": ["5"]}), fake.cmds)
    assert code == 1 and resp["error"]["code"] == "backend_error"


def test_status_squeue_then_sacct_then_lost(fake):
    fake.set("squeue", "10_0|RUNNING|None\n10_1|PENDING|Dependency\n")
    fake.set("sacct", "20|COMPLETED|0:0\n30|FAILED|3:0\n40|TIMEOUT|0:15\n")
    resp, code = slurm.handle(req("status", {"job_ids": ["10", "20", "30", "40", "50"]}), fake.cmds)
    assert code == 0
    st = {(s["job_id"], s["task"]): s for s in resp["result"]["states"]}
    assert st[("10", 0)]["state"] == "running" and st[("10", 1)]["reason"] == "Dependency"
    assert st[("20", None)]["state"] == "completed" and st[("20", None)]["exit_code"] == 0
    assert st[("30", None)]["state"] == "failed" and st[("30", None)]["exit_code"] == 3
    assert st[("40", None)]["exit_code"] == 128 + 15
    assert st[("50", None)]["state"] == "lost"
    assert fake.calls("squeue")[0][-2:] == ["-j", "10,20,30,40,50"]


def test_status_dependency_never_satisfied_is_cancelled(fake):
    fake.set("squeue", "8|PENDING|DependencyNeverSatisfied\n")
    resp, _ = slurm.handle(req("status", {"job_ids": ["8"]}), fake.cmds)
    assert resp["result"]["states"][0]["state"] == "cancelled"


def test_status_expands_pending_array_range(fake):
    fake.set("squeue", "9_[1-3]|PENDING|None\n")
    resp, _ = slurm.handle(req("status", {"job_ids": ["9"]}), fake.cmds)
    assert [s["task"] for s in resp["result"]["states"]] == [1, 2, 3]


def test_cancel_calls_scancel_and_reports_states(fake):
    fake.set("sacct", "5|CANCELLED by 1|0:0\n")
    resp, code = slurm.handle(req("cancel", {"job_ids": ["5"]}), fake.cmds)
    assert code == 0 and fake.calls("scancel") == [["5"]]
    assert resp["result"]["states"][0]["state"] == "cancelled"


def test_cancel_ended_job_not_an_error(fake):
    fake.set("scancel", "", rc=1, err="scancel: error: Invalid job id 5")
    fake.set("sacct", "5|COMPLETED|0:0\n")
    resp, code = slurm.handle(req("cancel", {"job_ids": ["5"]}), fake.cmds)
    assert code == 0 and resp["result"]["states"][0]["state"] == "completed"


def test_cancel_failure_is_backend_error(fake):
    fake.set("scancel", "", rc=1, err="scancel: error: socket timed out")
    resp, code = slurm.handle(req("cancel", {"job_ids": ["5"]}), fake.cmds)
    assert code == 1 and resp["error"]["code"] == "backend_error"


def test_envelope_errors(fake):
    assert slurm.handle("not json", fake.cmds)[1] == 2
    resp, code = slurm.handle(req("capabilities", version="2.0"), fake.cmds)
    assert code == 3 and resp["error"]["code"] == "unsupported_version"
    resp, code = slurm.handle(req("build"), fake.cmds)
    assert code == 1 and resp["error"]["code"] == "unsupported_operation"
    assert slurm.handle(req("capabilities", kind="container_runtime"), fake.cmds)[1] == 2


def test_missing_binary_is_backend_error(tmp_path):
    cmds = slurm.Commands(sbatch=str(tmp_path / "nope"), squeue=str(tmp_path / "nope"),
                          sacct=str(tmp_path / "nope"), scancel=str(tmp_path / "nope"))
    resp, code = slurm.handle(req("submit", {"jobs": [JOB], "submission_key": "k"}), cmds)
    assert code == 1 and resp["error"]["code"] == "backend_error"


def test_commands_from_env():
    c = slurm.Commands.from_env({"STRADLER_SLURM_SBATCH": "/x/sb"})
    assert c.sbatch == "/x/sb" and c.squeue == "squeue"


def test_main_stdio(fake):
    out = io.StringIO()
    code = slurm.main(stdin=io.StringIO(req("capabilities")), stdout=out, cmds=fake.cmds)
    assert code == 0 and json.loads(out.getvalue())["ok"] is True


def test_process_boundary_one_json_document(fake):
    env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(slurm.__file__), "..", ".."),
               STRADLER_SLURM_SBATCH=fake.cmds.sbatch)
    p = subprocess.run([sys.executable, "-m", "stradler.ext.slurm"], input=req("capabilities"),
                       capture_output=True, text=True, env=env, check=False)
    assert p.returncode == 0 and json.loads(p.stdout)["result"]["kind"] == "scheduler"
