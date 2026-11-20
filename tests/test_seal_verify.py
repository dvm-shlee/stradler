"""``stradler seal`` and ``stradler verify`` (docs/SNAPSHOTS.md sections 6 and 7)."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from stradler import seal as sealmod
from stradler.cli import main
from stradler.project import load_project
from stradler.seal import SealError, journal_path, rollback_seal, seal_dataset
from stradler.snapshot import snapshot_dir
from stradler.verify import verify_dataset

STRUCT = """schema = "structure@1.0.0"
[areas.rec]
path = "records"
role = "record"
mutability = "mutable"
[areas.out]
path = "out"
role = "data"
mutability = "sealed"
[areas.work]
path = "work"
role = "data"
mutability = "mutable"
"""


def _unlock(root: Path) -> None:
    for cur, dirs, files in os.walk(root):
        os.chmod(cur, 0o755)
        for f in files:
            p = os.path.join(cur, f)
            if not os.path.islink(p):
                os.chmod(p, 0o644)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / ".stradler").mkdir(parents=True)
    (root / "stradler.json").write_text(json.dumps({"marker": "stradler-project", "project_id": "t"}))
    (root / ".stradler" / "config.toml").write_text('schema = "config@1.0.0"\n')
    (root / ".stradler" / "structure.toml").write_text(STRUCT)
    ds = root / "out" / "ds"
    (ds / "sub").mkdir(parents=True)
    (ds / "a.txt").write_text("alpha")
    (ds / "sub" / "b.txt").write_text("beta")
    (ds / "sub" / "c.txt").write_text("gamma")
    yield root
    _unlock(tmp_path)


def _ctx(root):
    p = load_project(root)
    return str(root), p.structure, p.local


def _seal(root, **kw):
    r, s, loc = _ctx(root)
    return seal_dataset(r, s, "out/ds", local=loc, **kw)


def _docs(root):
    f = Path(snapshot_dir(root, load_project(root).structure))
    return sorted(p for p in f.glob("*.json"))


def _store(root, tmp_path, name="copy"):
    dest = tmp_path / "store"
    shutil.copytree(root / "out" / "ds", dest / "out" / "ds")
    _unlock(dest)
    (root / ".stradler" / "local.toml").write_text(f'[stores.{name}]\npath = "{dest}"\n')
    return dest / "out" / "ds"


def _modes(ds):
    return {str(p.relative_to(ds)): stat.S_IMODE(p.lstat().st_mode) for p in [ds, *ds.rglob("*")]}


# ---- seal ----


@pytest.mark.parametrize("opts", [{}, {"stream": True}, {"layout": "tree"}])
def test_seal_makes_read_only_writes_document_and_verifies(proj, opts):
    res = _seal(proj, **opts)
    assert res["result"] == "match" and res["sealed"] and res["counts"]["match"] == 3
    ds = proj / "out" / "ds"
    assert not any(m & 0o222 for m in _modes(ds).values())
    with pytest.raises(PermissionError):
        (ds / "x").write_text("no")
    assert len(_docs(proj)) == 1
    assert not os.path.exists(journal_path(str(proj), load_project(proj).structure, "out/ds"))


def test_seal_lineage_and_idempotent(proj):
    first = _seal(proj)
    again = _seal(proj)
    assert again["snapshot"] == first["snapshot"] and len(_docs(proj)) == 1
    ds = proj / "out" / "ds"
    _unlock(proj)
    (ds / "d.txt").write_text("delta")
    second = _seal(proj)
    assert second["snapshot"] != first["snapshot"]
    docs = {json.loads(p.read_text())["digest"]: json.loads(p.read_text()) for p in _docs(proj)}
    assert docs[second["snapshot"]]["previous"] == first["snapshot"]
    assert docs[first["snapshot"]]["previous"] is None


def test_seal_refuses_outside_sealed_area(proj):
    (proj / "work" / "w").mkdir(parents=True)
    (proj / "work" / "w" / "f").write_text("x")
    r, s, _loc = _ctx(proj)
    with pytest.raises(SealError, match="sealed"):
        seal_dataset(r, s, "work/w")
    assert stat.S_IMODE((proj / "work" / "w" / "f").stat().st_mode) & 0o222


def test_seal_stores_grade_and_provenance(proj):
    _seal(proj, grade="G1", provenance={"origin": "x"})
    d = json.loads(_docs(proj)[0].read_text())
    assert d["grade"] == "G1" and d["provenance"] == {"origin": "x"}


class Boom(Exception):
    pass


def _inject(monkeypatch, step, nth=1):
    n = {"k": 0}

    def hook(s):
        if s == step:
            n["k"] += 1
            if n["k"] == nth:
                raise Boom(s)

    monkeypatch.setattr(sealmod, "_checkpoint", hook)


@pytest.mark.parametrize("step,nth", [("journal", 1), ("chmod", 1), ("chmod", 3), ("permissions", 1),
                                       ("document", 1), ("cleanup", 1)])
def test_interrupted_seal_is_completed_by_rerun(proj, monkeypatch, step, nth):
    _inject(monkeypatch, step, nth)
    with pytest.raises(Boom):
        _seal(proj)
    j = journal_path(str(proj), load_project(proj).structure, "out/ds")
    assert os.path.exists(j) == (step != "cleanup")
    monkeypatch.undo()
    res = _seal(proj)
    assert res["result"] == "match" and res["sealed"]
    assert (res["resumed"]) == (step != "cleanup")
    assert not os.path.exists(j) and len(_docs(proj)) == 1
    assert not any(m & 0o222 for m in _modes(proj / "out" / "ds").values())


@pytest.mark.parametrize("step,nth", [("journal", 1), ("chmod", 2), ("permissions", 1), ("document", 1)])
def test_interrupted_seal_rollback_restores_everything(proj, monkeypatch, step, nth):
    ds = proj / "out" / "ds"
    (ds / "a.txt").chmod(0o640)
    before = _modes(ds)
    _inject(monkeypatch, step, nth)
    with pytest.raises(Boom):
        _seal(proj)
    monkeypatch.undo()
    r, s, _ = _ctx(proj)
    out = rollback_seal(r, s, "out/ds")
    assert out["restored"] == len(before)
    assert _modes(ds) == before
    assert _docs(proj) == []
    assert not os.path.exists(journal_path(r, s, "out/ds"))
    assert (ds / "a.txt").read_text() == "alpha"


def test_rerun_with_changed_content_refuses_and_names_rollback(proj, monkeypatch):
    _inject(monkeypatch, "permissions")
    with pytest.raises(Boom):
        _seal(proj)
    monkeypatch.undo()
    ds = proj / "out" / "ds"
    (ds / "a.txt").chmod(0o644)
    (ds / "a.txt").write_text("ALPHA")
    with pytest.raises(SealError, match=r"stradler seal --rollback out/ds"):
        _seal(proj)
    assert (ds / "a.txt").read_text() == "ALPHA"  # nothing lost
    r, s, _ = _ctx(proj)
    rollback_seal(r, s, "out/ds")
    assert (ds / "a.txt").read_text() == "ALPHA"


def test_stream_with_tree_layout_is_refused_before_any_change(proj):
    r, s, _ = _ctx(proj)
    with pytest.raises(SealError, match="stream"):
        seal_dataset(r, s, "out/ds", layout="tree", stream=True)
    assert all(m & 0o200 for m in _modes(proj / "out" / "ds").values())


def test_verify_reachable_store_without_dataset_reports_missing(proj, tmp_path):
    _seal(proj)
    (tmp_path / "empty").mkdir()
    (proj / ".stradler" / "local.toml").write_text(f'[stores.e]\npath = "{tmp_path}/empty"\n')
    r, s, loc = _ctx(proj)
    assert verify_dataset(r, s, "out/ds", store="e", local=loc)["counts"]["missing"] == 3


def test_rollback_without_journal_is_refused(proj):
    r, s, _ = _ctx(proj)
    with pytest.raises(SealError, match="no seal journal"):
        rollback_seal(r, s, "out/ds")


def test_journal_written_before_any_permission_change(proj, monkeypatch):
    seen = {}

    def hook(step):
        if step == "journal":
            seen["modes"] = _modes(proj / "out" / "ds")
            seen["journal"] = json.loads(Path(journal_path(str(proj), load_project(proj).structure, "out/ds")).read_text())

    monkeypatch.setattr(sealmod, "_checkpoint", hook)
    _seal(proj)
    assert all(m & 0o200 for m in seen["modes"].values())
    j = seen["journal"]
    assert j["dataset"] == "out/ds" and j["digest"].startswith("sha256:") and j["modes"]["a.txt"] == seen["modes"]["a.txt"]


# ---- verify ----


def test_verify_matches_then_reports_one_changed_byte(proj, tmp_path):
    _seal(proj)
    store = _store(proj, tmp_path)
    r, s, loc = _ctx(proj)
    assert verify_dataset(r, s, "out/ds", store="copy", local=loc)["result"] == "match"
    (store / "a.txt").write_text("alphX")
    res = verify_dataset(r, s, "out/ds", store="copy", local=loc)
    assert res["result"] == "differs" and res["counts"]["changed"] == 1 and res["lists"]["changed"] == ["a.txt"]


def test_verify_added_and_missing(proj, tmp_path):
    _seal(proj)
    store = _store(proj, tmp_path)
    (store / "extra.txt").write_text("e")
    (store / "sub" / "c.txt").unlink()
    r, s, loc = _ctx(proj)
    res = verify_dataset(r, s, "out/ds", store="copy", local=loc)
    assert res["counts"] == {"match": 2, "missing": 1, "added": 1, "changed": 0, "unreadable": 0}
    assert res["lists"] == {"added": ["extra.txt"], "missing": ["sub/c.txt"]}


def test_verify_symlink_is_added_and_unreadable_file(proj, tmp_path):
    _seal(proj, layout="tree")
    store = _store(proj, tmp_path)
    os.symlink("a.txt", store / "link")
    (store / "sub" / "b.txt").chmod(0)
    r, s, loc = _ctx(proj)
    res = verify_dataset(r, s, "out/ds", store="copy", local=loc)
    assert res["counts"]["added"] == 1 and res["counts"]["unreadable"] == 1


def test_verify_in_project_detects_missing_after_unlock(proj):
    _seal(proj)
    _unlock(proj)
    (proj / "out" / "ds" / "a.txt").unlink()
    r, s, _loc = _ctx(proj)
    assert verify_dataset(r, s, "out/ds")["counts"]["missing"] == 1


def test_verify_store_unreachable_and_record(proj, tmp_path):
    _seal(proj)
    (proj / ".stradler" / "local.toml").write_text(f'[stores.gone]\npath = "{tmp_path}/not-mounted"\n')
    r, s, loc = _ctx(proj)
    res = verify_dataset(r, s, "out/ds", store="gone", local=loc)
    assert res["result"] == "unreachable" and "counts" not in res
    rec = json.loads(Path(res["record"]).read_text())
    assert set(rec) == {"time", "store", "snapshot", "snapshot_version", "result"} and rec["store"] == "gone"


def test_check_record_has_no_paths_and_is_never_overwritten(proj):
    _seal(proj)
    r, s, _loc = _ctx(proj)
    a = verify_dataset(r, s, "out/ds")
    b = verify_dataset(r, s, "out/ds")
    assert a["record"] != b["record"] and os.path.exists(a["record"])
    text = Path(a["record"]).read_text()
    assert "a.txt" not in text and "sub" not in text
    rec = json.loads(text)
    assert set(rec) == {"time", "store", "snapshot", "snapshot_version", "result", "counts"}
    assert rec["counts"] == {"match": 3, "missing": 0, "added": 0, "changed": 0, "unreadable": 0}
    assert os.path.basename(a["record"]).split("-")[1] == "local"


def test_verify_never_writes_in_dataset_or_store(proj, tmp_path):
    _seal(proj)
    store = _store(proj, tmp_path)
    (store / "a.txt").write_text("changed")

    def snap(d):
        return {str(p.relative_to(d)): (p.lstat().st_mode, p.lstat().st_mtime_ns, p.lstat().st_size)
                for p in [d, *d.rglob("*")]}

    ds = proj / "out" / "ds"
    b1, b2 = snap(ds), snap(store)
    r, s, loc = _ctx(proj)
    verify_dataset(r, s, "out/ds", store="copy", local=loc)
    verify_dataset(r, s, "out/ds")
    assert snap(ds) == b1 and snap(store) == b2


def test_verify_by_digest_and_subtree(proj, tmp_path):
    res = _seal(proj, layout="tree")
    store = _store(proj, tmp_path)
    (store / "sub" / "b.txt").write_text("bXta")
    (store / "a.txt").write_text("changed outside")
    r, s, loc = _ctx(proj)
    sub = verify_dataset(r, s, res["snapshot"], store="copy", local=loc, subtree="sub")
    assert sub["counts"]["changed"] == 1 and sub["counts"]["match"] == 1
    assert verify_dataset(r, s, res["snapshot"], store="copy", local=loc)["counts"]["changed"] == 2
    from stradler.verify import VerifyError
    with pytest.raises(VerifyError, match="no directory"):
        verify_dataset(r, s, res["snapshot"], subtree="nope")


def test_subtree_needs_tree_layout(proj):
    _seal(proj)
    from stradler.verify import VerifyError
    r, s, _ = _ctx(proj)
    with pytest.raises(VerifyError, match="layout tree"):
        verify_dataset(r, s, "out/ds", subtree="sub")


def test_invalid_document_is_error_and_writes_no_record(proj):
    from stradler.verify import VerifyError
    _seal(proj)
    n_records = len(list((proj / "records" / "checks").glob("*.json")))
    doc = _docs(proj)[0]
    d = json.loads(doc.read_text())
    d["entries"][0]["size"] += 1
    doc.write_text(json.dumps(d))
    r, s, _ = _ctx(proj)
    with pytest.raises(VerifyError):
        verify_dataset(r, s, "out/ds")
    assert len(list((proj / "records" / "checks").glob("*.json"))) == n_records


# ---- command line ----


def test_cli_seal_and_verify_exit_codes(proj, tmp_path, capsys):
    ds = str(proj / "out" / "ds")
    assert main(["seal", ds, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["result"] == "match"
    store = _store(proj, tmp_path)
    assert main(["verify", ds, "--store", "copy"]) == 0
    (store / "a.txt").write_text("zzzzz")
    assert main(["verify", ds, "--store", "copy", "--json"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["counts"]["changed"] == 1
    (proj / ".stradler" / "local.toml").write_text(f'[stores.copy]\npath = "{tmp_path}/nowhere"\n')
    assert main(["verify", ds, "--store", "copy"]) == 2
    assert main(["verify", ds, "--store", "undefined"]) == 3
    assert main(["verify", str(proj / "out" / "nothing")]) == 3
    assert main(["verify"]) == 3


def test_cli_verify_digest_uses_cwd(proj, monkeypatch, capsys):
    res = _seal(proj)
    monkeypatch.chdir(proj)
    assert main(["verify", res["snapshot"]]) == 0


def test_cli_seal_refuses_unsealed_area_and_rollback(proj, monkeypatch, capsys):
    (proj / "work").mkdir()
    assert main(["seal", str(proj / "work")]) == 1
    _inject(monkeypatch, "permissions")
    with pytest.raises(Boom):
        main(["seal", str(proj / "out" / "ds")])
    monkeypatch.undo()
    assert main(["seal", "--rollback", str(proj / "out" / "ds")]) == 0
    assert main(["seal", "--rollback", str(proj / "out" / "ds")]) == 1


def test_cli_seal_provenance_file(proj, tmp_path):
    pf = tmp_path / "prov.json"
    pf.write_text('{"origin": "o1"}')
    assert main(["seal", str(proj / "out" / "ds"), "--layout", "tree", "--grade", "G", "--provenance-file", str(pf)]) == 0
    assert json.loads(_docs(proj)[0].read_text())["provenance"] == {"origin": "o1"}


def test_help_states_exit_codes(capsys):
    with pytest.raises(SystemExit) as e:
        main(["verify", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "0 match" in out and "2 unreachable" in out and "3 error" in out
