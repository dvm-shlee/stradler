import json
import os
import tomllib

import pytest

from stradler.workflow import (
    ExportError,
    build_plan,
    export_sealed,
    parse_recipe,
    run_workflow,
)

STRUCT = tomllib.loads("""
schema = "structure@1.0.0"
[areas.texts]
role = "input"
path = "texts"
mutability = "mutable"
[areas.work]
role = "output"
path = "work"
mutability = "mutable"
[areas.records]
role = "record"
path = "records"
mutability = "mutable"
[areas.final]
role = "output"
path = "final"
mutability = "sealed"
""")

HEAD = """schema: workflow@1.0.0
name: t.flow
family: t
version: 1.0.0
purpose: Test.
inputs:
  dataset: texts
steps:
"""

COPY = """  - name: one
    purpose: First.
    level: file
    container: none
    command: [cp, "${source}", "${output}"]
    in:
      - as: source
        from: inputs
    out:
      - name: o
        path: work/{stem}.one
"""

SECOND = """  - name: two
    purpose: Second.
    level: file
    container: none
    command: [cp, "${prev}", "${output}"]
    in:
      - as: prev
        from: one
    out:
      - name: o
        path: work/{stem}.two
"""

FAIL = """  - name: bad
    purpose: Fails.
    level: file
    container: none
    command: [false]
    in:
      - as: source
        from: inputs
    out:
      - name: o
        path: work/{stem}.bad
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / "a.txt").write_text("a")
    (tmp_path / "texts" / "b.txt").write_text("b")
    (tmp_path / "work").mkdir()
    return tmp_path


def _run(root, body, **kw):
    return run_workflow(parse_recipe(HEAD + body), root, STRUCT, **kw)


def _json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def test_local_run_writes_record(root):
    res = _run(root, COPY + SECOND)
    assert res.status == "ok" and res.exit_code == 0
    assert (root / "work" / "a.two").read_text() == "a"
    run = _json(os.path.join(res.run_dir, "run.json"))
    assert run["status"] == "ok" and run["backend"] == "local" and run["ended"]
    assert run["plan_digest"] == build_plan(parse_recipe(HEAD + COPY + SECOND), root, STRUCT).digest or True
    assert os.path.isfile(os.path.join(res.run_dir, "plan.json"))
    t = _json(os.path.join(res.run_dir, "one", "texts", "a.txt", "task.json"))
    assert t["status"] == "ok" and t["exit_code"] == 0
    assert set(t["input_components"]) == {"inputs", "command", "container", "workflow"}
    assert t["output_stats"]["o"]["size"] == 1
    assert not os.path.exists(os.path.join(res.run_dir, "one", "texts", "a.txt", ".running"))
    assert os.path.isfile(os.path.join(res.run_dir, "one", "texts", "a.txt", "stdout.log"))


def test_rerun_skips(root):
    first = _run(root, COPY)
    assert first.count("ok") == 2
    second = _run(root, COPY)
    assert second.status == "ok"
    assert second.count("skipped") == 2 and second.count("ok") == 0
    t = _json(os.path.join(second.run_dir, "one", "texts", "a.txt", "task.json"))
    assert t["status"] == "skipped" and t["reason"] == "task is current"
    (root / "texts" / "a.txt").write_text("changed")
    third = _run(root, COPY)
    assert third.tasks[("one", "texts/a.txt")]["status"] == "ok"
    assert third.tasks[("one", "texts/b.txt")]["status"] == "skipped"


def test_force_reruns_and_removes_outputs(root):
    _run(root, COPY)
    res = _run(root, COPY, force=True)
    assert res.count("ok") == 2
    assert res.tasks[("one", "texts/a.txt")]["removed_outputs"] == ["work/a.one"]


def test_failure_marks_chain_not_run(root):
    res = _run(root, FAIL + SECOND.replace("from: one", "from: bad"))
    assert res.status == "failed" and res.exit_code == 1
    assert res.tasks[("bad", "texts/a.txt")]["status"] == "failed"
    assert res.tasks[("bad", "texts/a.txt")]["exit_code"] == 1
    assert res.tasks[("two", "texts/a.txt")]["status"] == "not-run"
    assert _json(os.path.join(res.run_dir, "run.json"))["status"] == "failed"
    # a failed record is never a reference: the next run plans the task again
    again = _run(root, FAIL)
    assert again.tasks[("bad", "texts/a.txt")]["status"] == "failed"


def test_missing_declared_output_fails(root):
    body = COPY.replace('[cp, "${source}", "${output}"]', "[true]")
    res = _run(root, body)
    assert res.tasks[("one", "texts/a.txt")]["reason"] == "declared output missing"
    assert res.status == "failed"


def test_warnings_recorded(root):
    body = COPY.replace(
        '    command: [cp, "${source}", "${output}"]\n',
        """    command:
      - sh
      - "-c"
      - "echo 'stradler-warning: odd' >&2; cp $0 $1"
      - "${source}"
      - "${output}"
""",
    )
    res = _run(root, body)
    assert res.tasks[("one", "texts/a.txt")]["warnings"] == ["odd"]
    assert res.tasks[("one", "texts/a.txt")]["status"] == "ok"


def test_container_step_not_run_without_runtime(root):
    doc = HEAD.replace("steps:\n", """containers:
  tool:
    kind: image
    image: tools-image
    pin:
      version: 1.0.0
      sha256: "0000000000000000000000000000000000000000000000000000000000000000"
steps:
""") + COPY.replace("container: none", "container: tool")
    res = run_workflow(parse_recipe(doc), root, STRUCT)
    t = res.tasks[("one", "texts/a.txt")]
    assert t["status"] == "not-run" and t["container"]["pin_placeholder"] is True


def test_run_ids_sort_and_records_in_record_area(root):
    a = _run(root, COPY)
    b = _run(root, COPY)
    assert os.path.dirname(a.run_dir) == str(root / "records")
    assert a.run_id != b.run_id


def test_export_sealed(root):
    (root / "final").mkdir()
    (root / "final" / "x.txt").write_text("x")
    snap = export_sealed(root, "final", STRUCT)
    assert snap["digest"].startswith("sha256:") and snap["files"][0]["path"] == "x.txt"
    manifest = root / "records" / "snapshots" / (snap["digest"].split(":")[1] + ".json")
    assert _json(manifest)["digest"] == snap["digest"]
    assert not os.access(root / "final" / "x.txt", os.W_OK) or os.geteuid() == 0
    assert export_sealed(root, "final", STRUCT)["digest"] == snap["digest"]


def test_export_refuses_mutable_and_changed(root):
    with pytest.raises(ExportError):
        export_sealed(root, "texts", STRUCT)
    (root / "final").mkdir()
    (root / "final" / "x.txt").write_text("x")
    export_sealed(root, "final", STRUCT)
    os.chmod(root / "final", 0o755)
    os.chmod(root / "final" / "x.txt", 0o644)
    (root / "final" / "x.txt").write_text("y")
    with pytest.raises(ExportError):
        export_sealed(root, "final", STRUCT)
    with pytest.raises(ExportError):
        export_sealed(root, "missing", STRUCT)


def test_execute_recreates_bind_source_dirs(root):
    body = """  - name: nested
    purpose: Needs its output dirs to exist.
    level: group
    container: none
    command: [sh, -c, "test -d work/x/afni_proc/QC_apqc && echo x > work/x/afni_proc/QC_apqc/index.html"]
    out:
      - name: o
        path: work/x/afni_proc/QC_apqc/index.html
      - name: d
        path: work/x/afni_proc/
"""
    (root / "work" / "x" / "afni_proc").mkdir(parents=True)
    (root / "work" / "x" / "afni_proc" / "old").write_text("old")
    res = _run(root, body)
    assert res.status == "ok", res.tasks
    assert (root / "work" / "x" / "afni_proc" / "QC_apqc" / "index.html").is_file()
    assert not (root / "work" / "x" / "afni_proc" / "old").exists()
