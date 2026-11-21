"""Retention policy documents (docs/plans/VERSIONS.md section 2): lineage, one head, pins, CLI, validate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from stradler import schema as sch
from stradler.cli import main
from stradler.project import load_project
from stradler.retention import (
    RetentionError,
    check_document,
    effective_rules,
    pin_from_run,
    pin_version,
    read_policies,
    retention_dir,
    set_policy,
    show,
    unpin_version,
)
from stradler.snapshot import write_snapshot
from stradler.validate import validate_project

STRUCT_TOML = """schema = "structure@1.0.0"
[areas.rec]
path = "records"
role = "record"
mutability = "mutable"
[areas.out]
path = "out"
role = "output"
mutability = "mutable"
"""
STRUCT = {"areas": {"rec": {"path": "records", "role": "record"}, "out": {"path": "out", "role": "output"}}}
DS = "out/ds"
T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
T2 = "2026-01-03T00:00:00Z"
T3 = "2026-01-04T00:00:00Z"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".stradler").mkdir(parents=True)
    (root / "stradler.json").write_text(json.dumps({
        "marker": "stradler-project", "project_id": "t", "spec_version": "1.0.0", "config_dir": ".stradler"}))
    (root / ".stradler" / "structure.toml").write_text(STRUCT_TOML)
    (root / ".stradler" / "config.toml").write_text('schema = "config@1.0.0"\n')
    (root / DS).mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def versions(root: Path, n: int = 3) -> list[str]:
    """Seal n versions of the dataset and return their digests, oldest first."""
    out: list[str] = []
    for i in range(n):
        (root / DS / "a.txt").write_text(f"content {i}")
        doc = write_snapshot(root, DS, STRUCT, previous=out[-1] if out else None, created=f"2026-01-0{i + 1}T00:00:00Z")
        out.append(doc["digest"])
    return out


def policy_files(root: Path) -> list[str]:
    d = Path(retention_dir(root, STRUCT))
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


def test_schema_is_bundled_and_valid_document_passes(proj):
    assert sch.load_bundled("retention")["$id"] == "retention@1.0.0"
    res = set_policy(proj, STRUCT, dataset=DS, keep_last=3, created=T0)
    doc = res["document"]
    assert res["written"] and check_document(doc) == []
    assert doc["retention_version"] == 1 and doc["previous"] is None and doc["hold_copies"] == 1
    assert doc["keep_days"] is None and doc["pins"] == []
    assert policy_files(proj) == [doc["digest"].split(":")[1] + ".json"]
    assert Path(retention_dir(proj, STRUCT)).parts[-3:] == ("records", "retention", "v1")


def test_digest_excludes_previous_and_created(proj):
    a = set_policy(proj, STRUCT, dataset=DS, keep_last=3, created=T0)["document"]
    b = set_policy(proj, STRUCT, area="out", keep_last=3, created=T1)["document"]
    c = set_policy(proj, STRUCT, area="out", keep_days=5, created=T2)["document"]
    assert a["digest"] != b["digest"]  # the scope is digested
    assert c["previous"] == b["digest"] and c["digest"] != b["digest"]


def test_lineage_and_unchanged_set(proj):
    r1 = set_policy(proj, STRUCT, dataset=DS, keep_last=2, created=T0)["document"]
    r2 = set_policy(proj, STRUCT, dataset=DS, keep_days=30, hold_copies=2, created=T1)["document"]
    assert r2["previous"] == r1["digest"] and r2["keep_last"] == 2 and r2["hold_copies"] == 2
    same = set_policy(proj, STRUCT, dataset=DS, keep_days=30, created=T2)
    assert same["written"] is False and same["document"]["digest"] == r2["digest"]
    assert len(policy_files(proj)) == 2
    out = show(proj, STRUCT, dataset=DS)
    assert [r["digest"] for r in out["revisions"]] == [r2["digest"], r1["digest"]]
    assert out["head"]["digest"] == r2["digest"]


def test_clear_rules(proj):
    set_policy(proj, STRUCT, dataset=DS, keep_last=2, keep_days=7, created=T0)
    doc = set_policy(proj, STRUCT, dataset=DS, keep_last=None, created=T1)["document"]
    assert doc["keep_last"] is None and doc["keep_days"] == 7


def test_invalid_values_and_scopes(proj):
    for kw in ({"keep_last": 0}, {"keep_days": -1}, {"hold_copies": 0}, {"hold_copies": None}, {"keep_last": True}):
        with pytest.raises(RetentionError):
            set_policy(proj, STRUCT, dataset=DS, **kw)
    with pytest.raises(RetentionError, match="unknown area"):
        set_policy(proj, STRUCT, area="nope")
    with pytest.raises(RetentionError, match="lies in no area"):
        set_policy(proj, STRUCT, dataset="elsewhere/ds")
    with pytest.raises(RetentionError, match="project-relative"):
        set_policy(proj, STRUCT, dataset="../x")
    with pytest.raises(RetentionError, match="exactly one"):
        set_policy(proj, STRUCT, dataset=DS, area="out")
    with pytest.raises(RetentionError, match="exactly one"):
        show(proj, STRUCT)
    assert policy_files(proj) == []


def test_pin_by_digest_and_unpin(proj):
    v = versions(proj)
    res = pin_version(proj, STRUCT, DS, v[0], "published in paper", created=T1)
    doc = res["document"]
    assert res["written"] and doc["pins"] == [
        {"snapshot": v[0], "snapshot_version": 2, "reason": "published in paper", "added": T1}]
    assert check_document(doc) == []
    doc2 = pin_version(proj, STRUCT, DS, v[1], "second", created=T2)["document"]
    assert doc2["previous"] == doc["digest"]
    assert [p["snapshot"] for p in doc2["pins"]] == sorted([v[0], v[1]])
    with pytest.raises(RetentionError, match="already pinned"):
        pin_version(proj, STRUCT, DS, v[0], "again")
    # unpin writes a new revision of rules that no earlier revision had
    doc3 = unpin_version(proj, STRUCT, DS, v[0], created=T3)["document"]
    assert doc3["previous"] == doc2["digest"] and [p["snapshot"] for p in doc3["pins"]] == [v[1]]
    with pytest.raises(RetentionError, match="not pinned"):
        unpin_version(proj, STRUCT, DS, v[0])
    assert len(policy_files(proj)) == 3


def test_pin_unpin_pin_gives_new_files(proj):
    v = versions(proj, 1)
    r0 = set_policy(proj, STRUCT, dataset=DS, keep_last=2, created=T0)["document"]
    a = pin_version(proj, STRUCT, DS, v[0], "x", created=T1)["document"]
    b = unpin_version(proj, STRUCT, DS, v[0], created=T2)["document"]
    c = pin_version(proj, STRUCT, DS, v[0], "x", created=T1)["document"]
    assert b["rules_digest"] == r0["rules_digest"] and b["digest"] != r0["digest"]
    assert c["rules_digest"] == a["rules_digest"] and c["digest"] != a["digest"]
    assert c["previous"] == b["digest"]
    assert len(policy_files(proj)) == 4
    assert show(proj, STRUCT, dataset=DS)["head"]["digest"] == c["digest"]


def test_pin_outside_lineage_refused(proj):
    versions(proj)
    with pytest.raises(RetentionError, match="not a version of dataset"):
        pin_version(proj, STRUCT, DS, "sha256:" + "0" * 64, "x")
    with pytest.raises(RetentionError, match="not a digest"):
        pin_version(proj, STRUCT, DS, "sha256:abc", "x")
    assert policy_files(proj) == []


def test_pin_digest_of_another_dataset_refused(proj):
    v = versions(proj, 1)
    other = proj / "out" / "other"
    other.mkdir()
    (other / "f.txt").write_text("other")
    write_snapshot(proj, "out/other", STRUCT, created=T0)
    with pytest.raises(RetentionError, match="not a version"):
        pin_version(proj, STRUCT, "out/other", v[0], "x")


def test_reason_rules(proj):
    v = versions(proj, 1)
    for bad in ("", "  ", "x" * 201, "see /data/run", "a\\b"):
        with pytest.raises(RetentionError):
            pin_version(proj, STRUCT, DS, v[0], bad)
    assert pin_version(proj, STRUCT, DS, v[0], "x" * 200)["written"]


def test_area_scope_has_no_pins(proj):
    doc = set_policy(proj, STRUCT, area="out", keep_last=1, created=T0)["document"]
    assert doc["scope"] == {"kind": "area", "area": "out"} and doc["pins"] == []
    bad = dict(doc, pins=[{"snapshot": "sha256:" + "1" * 64, "snapshot_version": 2, "reason": "r", "added": T0}])
    assert any("only in a dataset scope" in m or "digest" in m for m in check_document(bad))


def _two_heads(proj):
    """Two clones of the record area each wrote a revision with the same previous."""
    r1 = set_policy(proj, STRUCT, dataset=DS, keep_last=2, created=T0)["document"]
    a = set_policy(proj, STRUCT, dataset=DS, keep_days=10, created=T1)["document"]
    folder = Path(retention_dir(proj, STRUCT))
    # a sibling revision of r1 written elsewhere: same previous, different rules
    from stradler.retention import _build
    b = _build({"kind": "dataset", "dataset": DS},
               {"keep_last": 5, "keep_days": 2, "hold_copies": 3, "pins": []}, r1["digest"], T2)
    (folder / (b["digest"].split(":")[1] + ".json")).write_text(json.dumps(b))
    return r1, a, b


def test_two_heads_refused_by_every_reader_and_writer(proj):
    versions(proj, 1)
    r1, a, b = _two_heads(proj)
    msg = "2 policy heads"
    with pytest.raises(RetentionError, match=msg):
        show(proj, STRUCT, dataset=DS)
    with pytest.raises(RetentionError, match=msg):
        set_policy(proj, STRUCT, dataset=DS, keep_last=9)
    with pytest.raises(RetentionError, match=msg):
        effective_rules(proj, STRUCT, DS)
    snap = [d for d in (proj / "records" / "snapshots" / "v2").glob("*.json")][0].stem
    with pytest.raises(RetentionError, match=msg):
        pin_version(proj, STRUCT, DS, "sha256:" + snap, "x")
    with pytest.raises(RetentionError, match=msg):
        unpin_version(proj, STRUCT, DS, "sha256:" + snap)
    n = len(policy_files(proj))
    assert n == 3  # nothing was written by the refused operations
    prj = load_project(proj)
    assert any("2 policy heads" in p for p in validate_project(prj))
    assert main(["retention", "show", DS]) == 3


def test_merge_resolves_two_heads_by_union(proj):
    versions(proj, 1)
    r1, a, b = _two_heads(proj)
    heads = sorted([a["digest"], b["digest"]])
    with pytest.raises(RetentionError, match="exactly two heads"):
        set_policy(proj, STRUCT, area="out", merge=True)
    res = set_policy(proj, STRUCT, dataset=DS, merge=True, created=T3)
    m = res["document"]
    assert res["written"] and m["previous"] == heads[0] and m["merges"] == heads[1]
    assert (m["keep_last"], m["keep_days"], m["hold_copies"]) == (5, 10, 3)
    assert check_document(m) == []
    out = show(proj, STRUCT, dataset=DS)
    assert out["head"]["digest"] == m["digest"]
    prj = load_project(proj)
    assert validate_project(prj) == []
    # one head again: a normal set works
    assert set_policy(proj, STRUCT, dataset=DS, keep_last=6, created="2026-02-01T00:00:00Z")["written"]


def test_effective_rules_union_of_area_and_dataset(proj):
    v = versions(proj, 2)
    set_policy(proj, STRUCT, area="out", keep_last=5, keep_days=3, hold_copies=2, created=T0)
    set_policy(proj, STRUCT, dataset=DS, keep_last=2, keep_days=30, created=T1)
    pin_version(proj, STRUCT, DS, v[0], "paper", created=T2)
    e = effective_rules(proj, STRUCT, DS)
    assert (e["keep_last"], e["keep_days"], e["hold_copies"]) == (5, 30, 2)
    assert [p["snapshot"] for p in e["pins"]] == [v[0]] and len(e["policies"]) == 2
    out = show(proj, STRUCT, dataset=DS)
    assert out["effective"] == e
    assert "effective" not in show(proj, STRUCT, area="out")


def test_no_policy_shows_nothing(proj):
    out = show(proj, STRUCT, dataset=DS)
    assert out["head"] is None and out["revisions"] == []
    assert out["effective"]["keep_last"] is None and out["effective"]["pins"] == []


def test_atomic_write_leaves_no_temporary_file(proj):
    set_policy(proj, STRUCT, dataset=DS, keep_last=2)
    assert not [f for f in os.listdir(retention_dir(proj, STRUCT)) if ".tmp-" in f]


def test_read_rejects_unknown_key_and_tampered_digest(proj):
    doc = set_policy(proj, STRUCT, dataset=DS, keep_last=2, created=T0)["document"]
    assert any("unknown key" in m for m in check_document(dict(doc, extra=1)))
    assert "digest does not match" in " ".join(check_document(dict(doc, keep_last=9)))
    assert check_document(doc, "wrong.json")
    path = Path(retention_dir(proj, STRUCT)) / (doc["digest"].split(":")[1] + ".json")
    path.write_text(json.dumps(dict(doc, keep_last=9)))
    scopes, problems = read_policies(proj, STRUCT)
    assert scopes == {} and problems
    with pytest.raises(RetentionError, match="invalid policy"):
        set_policy(proj, STRUCT, dataset=DS, keep_last=3)


def test_validate_reports_pin_outside_lineage_and_bad_scope(proj):
    versions(proj, 1)
    from stradler.retention import _build
    folder = Path(retention_dir(proj, STRUCT))
    folder.mkdir(parents=True, exist_ok=True)
    pins = [{"snapshot": "sha256:" + "2" * 64, "snapshot_version": 2, "reason": "r", "added": T0}]
    d = _build({"kind": "dataset", "dataset": DS}, {"keep_last": None, "keep_days": None, "hold_copies": 1, "pins": pins},
               None, T0)
    a = _build({"kind": "area", "area": "gone"}, {"keep_last": 1, "keep_days": None, "hold_copies": 1, "pins": []},
               None, T0)
    for x in (d, a):
        (folder / (x["digest"].split(":")[1] + ".json")).write_text(json.dumps(x))
    problems = validate_project(load_project(proj))
    assert any("pin sha256:" in p and "not a version" in p for p in problems)
    assert any("unknown area" in p for p in problems)


def test_validate_clean_project_unchanged(proj):
    versions(proj, 2)
    set_policy(proj, STRUCT, dataset=DS, keep_last=2)
    assert validate_project(load_project(proj)) == []


# ---------------------------------------------------------------- pin --from-run


def _run_record(root: Path, entries: list[dict], run_id: str = "run-1") -> Path:
    d = root / "records" / run_id
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"run_id": run_id, "inputs": {"datasets": entries}}))
    return d


def test_pin_from_run(proj):
    v = versions(proj, 2)
    run = _run_record(proj, [{"path": DS, "snapshot": v[0]}, {"path": DS, "snapshot": v[1]}])
    res = pin_from_run(proj, STRUCT, run, created=T1)
    assert res["run_id"] == "run-1" and [p["snapshot"] for p in res["pins"]] == sorted(v)
    assert all(p["written"] for p in res["pins"])
    head = show(proj, STRUCT, dataset=DS)["head"]
    assert [p["snapshot"] for p in head["pins"]] == sorted(v)
    assert {p["reason"] for p in head["pins"]} == {"input of run run-1"}
    again = pin_from_run(proj, STRUCT, run / "run.json")
    assert not any(p["written"] for p in again["pins"])


def test_pin_from_run_refusals_write_nothing(proj):
    v = versions(proj, 1)
    with pytest.raises(RetentionError, match="cannot read run record"):
        pin_from_run(proj, STRUCT, proj / "records" / "missing")
    run = _run_record(proj, [{"path": DS, "revision": None}], "run-a")
    with pytest.raises(RetentionError, match="names no input snapshot"):
        pin_from_run(proj, STRUCT, run)
    run = _run_record(proj, [{"path": DS, "snapshot": v[0]}, {"path": DS, "snapshot": "sha256:" + "0" * 64}], "run-b")
    with pytest.raises(RetentionError, match="not a version"):
        pin_from_run(proj, STRUCT, run)
    assert policy_files(proj) == []


# ---------------------------------------------------------------- CLI


def test_cli_set_show_pin_unpin(proj, capsys):
    v = versions(proj, 2)
    assert main(["retention", "set", DS, "--keep-last", "3", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["written"] and out["document"]["keep_last"] == 3
    assert main(["retention", "set", DS, "--keep-days", "9", "--hold-copies", "2", "--no-keep-last"]) == 0
    assert capsys.readouterr().out.startswith("wrote sha256:")
    assert main(["retention", "pin", DS, v[0], "--reason", "paper", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["document"]["pins"][0]["snapshot"] == v[0]
    assert main(["retention", "show", DS, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["head"]["keep_days"] == 9 and shown["head"]["keep_last"] is None and len(shown["revisions"]) == 3
    assert main(["retention", "show", DS]) == 0
    assert "effective: keep_last None  keep_days 9  hold_copies 2  pins 1" in capsys.readouterr().out
    assert main(["retention", "pin", DS, v[1], "--reason", "second"]) == 0
    assert main(["retention", "unpin", DS, v[0]]) == 0
    assert main(["retention", "set", "--area", "out", "--keep-last", "4"]) == 0
    capsys.readouterr()
    assert main(["retention", "show", "--area", "out", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["head"]["keep_last"] == 4


def test_cli_errors_exit_3(proj, capsys):
    v = versions(proj, 1)
    assert main(["retention", "pin", DS, "sha256:" + "0" * 64, "--reason", "r", "--json"]) == 3
    assert json.loads(capsys.readouterr().err)["ok"] is False
    assert main(["retention", "pin", DS, v[0]]) == 3  # no reason
    assert main(["retention", "pin", DS]) == 3
    assert main(["retention", "set", "--area", "nope", "--keep-last", "1"]) == 3
    assert main(["retention", "set", DS, "--keep-last", "0"]) == 3
    assert main(["retention", "unpin", DS, v[0]]) == 3
    assert main(["retention", "show"]) == 3
    assert policy_files(proj) == []


def test_cli_pin_from_run(proj, capsys):
    v = versions(proj, 1)
    run = _run_record(proj, [{"path": DS, "snapshot": v[0]}])
    assert main(["retention", "pin", "--from-run", str(run), "--reason", "rerun input"]) == 0
    assert f"{DS}: pinned {v[0]}" in capsys.readouterr().out
    assert main(["retention", "pin", DS, "--from-run", str(run)]) == 3
