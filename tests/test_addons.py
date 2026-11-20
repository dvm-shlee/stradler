import hashlib
import json
import os
import subprocess
import sys

import pytest

from stradler.addons import Addon, AddonError, PlanRefused, core_platform, load_addons
from stradler.workflow import run_workflow, scheduled
from stradler.workflow.plan import PlanError
from stradler.workflow.schema import load_recipe

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
FAKES = os.path.join(HERE, "fakes")
PLAT = core_platform()

STRUCT = {"areas": {
    "texts": {"role": "input", "path": "texts", "mutability": "mutable"},
    "work": {"role": "output", "path": "work", "mutability": "mutable"},
    "images": {"role": "input", "path": "images", "mutability": "sealed"},
    "records": {"role": "record", "path": "records", "mutability": "mutable"},
}}

RECIPE = f"""schema: workflow@1.0.0
name: t.flow
family: t
version: 1.0.0
purpose: Test.
inputs:
  dataset: texts
steps:
  - name: one
    purpose: Copy each file.
    level: file
    container: none
    resources:
      cpus: 2
      memory: 1G
    command: [cp, "${{source}}", "${{output}}"]
    in:
      - as: source
        from: inputs
    out:
      - name: o
        path: work/{{stem}}.one
  - name: all
    purpose: Join the copies.
    level: group
    container: none
    resources:
      memory: 2G
    command: [{sys.executable}, {FAKES}/join_files.py, "${{output}}", "${{parts}}"]
    in:
      - as: parts
        from: one
        select:
          for_each: file
    out:
      - name: t
        path: work/total.txt
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", SRC)
    for d in ("texts", "work", "images", "recipes", ".stradler"):
        (tmp_path / d).mkdir()
    (tmp_path / "texts" / "a.txt").write_text("a")
    (tmp_path / "texts" / "b.txt").write_text("b")
    (tmp_path / "images" / "tool.sif").write_bytes(b"image-bytes")
    (tmp_path / "recipes" / "flow.yaml").write_text(RECIPE)
    return tmp_path


def tval(v):
    """Render a JSON-like value as TOML."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(tval(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {tval(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)


def wrapper(root, name):
    """An executable taking no arguments that runs a fake add-on (ADDONS 1.7)."""
    path = root / f"{name}.sh"
    path.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKES}/{name}.py "$@"\n')
    path.chmod(0o755)
    return str(path)


def configure(root, sched_caps=None, lose=(), runtime=True, rt_caps=None):
    caps = {"platforms": [PLAT]}
    caps.update(sched_caps or {})
    sched_cmd = wrapper(root, "fake_scheduler")
    text = ['schema = "config@1.0.0"', "", "[addons.sched]", 'kind = "scheduler"', 'version = "0.1.0"',
            f"command = {json.dumps(sched_cmd)}", "",
            "[addons.sched.settings]", f"state = {json.dumps(str(root / 'sched-state.json'))}",
            f"lose = {tval(list(lose))}", f"caps = {tval(caps)}", ""]
    if runtime:
        rcaps = {"platforms": [PLAT]}
        rcaps.update(rt_caps or {})
        rt_cmd = wrapper(root, "fake_runtime")
        text += ["[addons.rt]", 'kind = "container_runtime"', 'version = "0.1.0"',
                 f"command = {json.dumps(rt_cmd)}", "",
                 "[addons.rt.settings]", f"caps = {tval(rcaps)}"]
    (root / ".stradler" / "config.toml").write_text("\n".join(text) + "\n")
    return load_addons(root)


def go(root, addons):
    path = "recipes/flow.yaml"
    return run_workflow(load_recipe(root / path), root, STRUCT, recipe_file=path, addons=addons,
                        scheduler="sched", job_prefix="t")


def state(root):
    return json.loads((root / "sched-state.json").read_text())


def run_json(res):
    with open(os.path.join(res.run_dir, "run.json"), encoding="utf-8") as fh:
        return json.load(fh)


def submits(root):
    return [r["params"] for r in state(root)["requests"] if r["op"] == "submit"]


def test_addon_discovery_and_protocol_errors(project):
    addons = configure(project)
    assert set(addons) == {"sched", "rt"}
    assert addons["rt"].kind == "container_runtime" and addons["rt"].version == "0.1.0"
    assert addons["rt"].capabilities()["bind_modes"] == ["ro", "rw"]
    with pytest.raises(AddonError) as e:
        addons["rt"].call("build")
    assert e.value.code == "unsupported_operation"
    broken = Addon("x", "scheduler", "0.1.0", [sys.executable, "-c", "print('not json')"])
    with pytest.raises(AddonError) as e:
        broken.call("capabilities")
    assert e.value.code == "internal"
    liar = Addon("y", "scheduler", "0.1.0", [sys.executable, "-c",
                 "import json;print(json.dumps({'protocol_version':'1.0','ok':True,'result':{}}));raise SystemExit(1)"])
    with pytest.raises(AddonError) as e:
        liar.call("capabilities")
    assert e.value.code == "internal"
    old = Addon("z", "scheduler", "0.1.0", [sys.executable, "-c",
                "import json;print(json.dumps({'protocol_version':'2.0','ok':True,'result':{}}))"])
    with pytest.raises(AddonError) as e:
        old.call("capabilities")
    assert e.value.code == "unsupported_version"
    wrong_kind = Addon("w", "scheduler", "0.1.0", addons["rt"].command, addons["rt"].settings)
    with pytest.raises(PlanRefused) as e:
        wrong_kind.capabilities()
    assert e.value.code == "incompatible.kind"


def test_addon_array_and_end_dependencies(project):
    addons = configure(project, runtime=False)
    res = go(project, addons)
    assert res.status == "running"  # non-blocking: the call returned before any job ran
    first, second, fin = submits(project)
    (j1,), (j2,), (jf,) = first["jobs"], second["jobs"], fin["jobs"]
    assert j1["array"] == 2 and j1["dependencies"] == []
    assert j1["resources"] == {"cpus": 2, "memory": "1G"}
    assert j2["array"] is None and j2["dependencies"] == [{"on": "end", "jobs": ["j1"]}]
    assert j2["resources"] == {"memory": "2G"}
    assert jf["dependencies"] == [{"on": "end", "jobs": ["j1", "j2"]}]
    assert jf["argv"][-3:] == ["workflow", "finalize", res.run_dir]
    assert len({first["submission_key"], second["submission_key"], fin["submission_key"]}) == 3
    doc = run_json(res)
    assert doc["status"] == "running" and doc["backend"] == "sched" and doc["backend_version"] == "0.1.0"
    assert [(s["job_id"], s["array_size"]) for s in doc["segments"]] == [("j1", 2), ("j2", None)]
    assert doc["segments"][0]["job_name"] == "t-t-one" and doc["segments"][1]["job_name"].startswith("t-t-all")
    assert "settings" not in json.dumps(doc)


def test_addon_finalize_job_runs_last_and_writes_run_json(project):
    addons = configure(project, runtime=False)
    res = go(project, addons)
    assert run_json(res)["status"] == "running"
    addons["sched"].call("drain")
    assert [j["state"] for j in state(project)["jobs"].values()] == ["completed"] * 3
    doc = run_json(res)
    assert doc["status"] == "ok" and doc["exit_code"] == 0 and doc["ended"]
    assert doc["finalize_job_id"] == "j3" and "missing_tasks" not in doc
    assert (project / "work" / "total.txt").read_text() == "ab"


def test_addon_finalize_missing_record_is_failed(project):
    addons = configure(project, runtime=False, lose=["t-t-one"])
    res = go(project, addons)
    addons["sched"].call("drain")
    doc = run_json(res)
    assert doc["status"] == "failed" and doc["exit_code"] == 1 and doc["finalize_job_id"] == "j3"
    assert {"step": "one", "unit": "texts/a.txt"} in doc["missing_tasks"]
    # F wrote no task record for the task that never ran
    assert not os.path.exists(os.path.join(res.run_dir, "one", "texts", "a.txt", "task.json"))


def test_addon_finalize_task_failure_marks_run_failed(project):
    (project / "recipes" / "flow.yaml").write_text(RECIPE.replace('[cp, "${source}"', '[cp, "/nonexistent-source"', 1))
    addons = configure(project, runtime=False)
    res = go(project, addons)
    addons["sched"].call("drain")
    doc = run_json(res)
    assert doc["status"] == "failed" and "missing_tasks" not in doc
    recs = scheduled.read_records(res.run_dir)
    assert recs[("one", "texts/a.txt")]["status"] == "failed"
    assert all(r["status"] in ("failed", "not-run") for r in recs.values())


def test_addon_finalize_is_idempotent_and_honours_cancel_marker(project):
    addons = configure(project, runtime=False)
    res = go(project, addons)
    scheduled.cancel_run(res.run_dir, addons["sched"])
    assert os.path.exists(os.path.join(res.run_dir, ".cancelled"))
    # the finalize job is not cancelled: it writes the interrupted status
    assert [j["state"] for j in state(project)["jobs"].values()] == ["cancelled", "cancelled", "pending"]
    doc = scheduled.finalize_run(res.run_dir, job_id="jx")
    assert doc["status"] == "interrupted" and doc["finalize_job_id"] == "jx"
    assert scheduled.finalize_run(res.run_dir, job_id="jy") == doc


def test_addon_finalize_fallback_reader(project):
    addons = configure(project, runtime=False, lose=["t-t-finalize"])
    res = go(project, addons)
    addons["sched"].call("drain")
    assert run_json(res)["status"] == "running"  # F never ran
    doc = scheduled.collect_run(res.run_dir, addons["sched"])
    assert doc["status"] == "ok" and doc["exit_code"] == 0
    assert scheduled.collect_run(res.run_dir, addons["sched"]) == doc


def test_addon_finalize_entry_point_runs_as_process(project):
    addons = configure(project, runtime=False)
    res = go(project, addons)
    for seg, n in ((0, 2), (1, 1)):
        for i in range(n):
            subprocess.run([sys.executable, "-m", "stradler", "workflow", "chain", res.run_dir, str(seg)],
                           env=dict(os.environ, STRADLER_TASK_INDEX=str(i)), check=True)
    subprocess.run([sys.executable, "-m", "stradler", "workflow", "finalize", res.run_dir],
                   env=dict(os.environ, STRADLER_JOB_ID="j9"), check=True)
    assert run_json(res)["status"] == "ok" and run_json(res)["finalize_job_id"] == "j9"


def test_addon_blocking_scheduler_has_no_finalize_job(project):
    addons = configure(project, sched_caps={"non_blocking": False}, runtime=False)
    res = go(project, addons)
    assert len(submits(project)) == 2
    doc = run_json(res)
    assert res.status == "ok" and doc["status"] == "ok" and "finalize_job_id" not in doc


def with_container(project, image_sha=None):
    sha = image_sha or hashlib.sha256(b"image-bytes").hexdigest()
    text = RECIPE.replace("steps:\n", f"""containers:
  tool:
    kind: image
    image: tools-image
    pin:
      version: 1.0.0
      sha256: "{sha}"
      path: images/tool.sif
steps:
""", 1).replace("container: none", "container: tool", 1)
    (project / "recipes" / "flow.yaml").write_text(text)


def test_addon_runtime_wrap_records_format_and_digest(project):
    with_container(project)
    addons = configure(project)
    res = go(project, addons)
    addons["sched"].call("drain")
    assert run_json(res)["status"] == "ok"
    c = scheduled.read_records(res.run_dir)[("one", "texts/a.txt")]["container"]
    digest = "sha256:" + hashlib.sha256(b"image-bytes").hexdigest()
    assert c == {"name": "tool", "kind": "image", "pin_digest": digest, "resolved_digest": digest,
                 "format": "sif", "pin_matches": True}
    assert (project / "work" / "a.one").read_text() == "a"


def test_addon_runtime_pin_mismatch_is_recorded_not_failed(project):
    with_container(project, image_sha="1" * 64)
    addons = configure(project)
    res = go(project, addons)
    addons["sched"].call("drain")
    rec = scheduled.read_records(res.run_dir)[("one", "texts/a.txt")]
    assert rec["status"] == "ok" and rec["container"]["pin_matches"] is False


def test_addon_pairing_refusal_writes_nothing(project):
    with_container(project)
    addons = configure(project, sched_caps={"container_image_formats": ["oci"]})
    with pytest.raises(PlanRefused) as e:
        go(project, addons)
    assert e.value.code == "incompatible.pairing"
    msg = str(e.value)
    assert msg.startswith('stradler: plan refused: incompatible.pairing: scheduler "sched" 0.1.0 ')
    assert 'image format "sif"' in msg and 'container "tool"' in msg and "settings" not in msg
    assert not (project / "records").exists()
    assert submits(project) == []


@pytest.mark.parametrize("caps, rt_caps, code", [
    ({"dependency_kinds": ["success"]}, None, "incompatible.dependency"),
    ({"array_jobs": False}, None, "incompatible.array"),
    ({"resources": ["cpus"]}, None, "incompatible.resource"),
    ({"job_name_max": 4}, None, "incompatible.name"),
    ({"platforms": ["nowhere"]}, None, "incompatible.platform"),
    ({}, {"digest_algorithms": ["md5"]}, "incompatible.digest"),
    ({}, {"bind_modes": ["ro"]}, "incompatible.bind_mode"),
    ({}, {"image_formats": ["oci"]}, "incompatible.image_format"),
])
def test_addon_refusal_codes(project, caps, rt_caps, code):
    with_container(project)
    addons = configure(project, sched_caps=caps, rt_caps=rt_caps)
    with pytest.raises(PlanRefused) as e:
        go(project, addons)
    assert e.value.code == code and f"plan refused: {code}:" in str(e.value)
    assert not (project / "records").exists()


def test_addon_missing_entries_and_recipe_file(project):
    addons = configure(project, runtime=False)
    with pytest.raises(PlanRefused) as e:
        go(project, {})
    assert e.value.code == "incompatible.missing_add_on"
    with_container(project)
    with pytest.raises(PlanRefused) as e:
        go(project, addons)
    assert e.value.code == "incompatible.missing_add_on"
    with pytest.raises(PlanError):
        run_workflow(load_recipe(project / "recipes" / "flow.yaml"), project, STRUCT, addons=addons,
                     scheduler="sched")


def test_local_scheduler_unchanged_without_add_on(project):
    res = run_workflow(load_recipe(project / "recipes" / "flow.yaml"), project, STRUCT)
    assert res.status == "ok" and run_json(res)["backend"] == "local"
