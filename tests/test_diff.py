"""``stradler diff``: snapshot against snapshot (docs/plans/VERSIONS.md section 3)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from stradler.cli import main
from stradler.diff import DiffError, classify, diff_dataset
from stradler.project import load_project
from stradler.seal import seal_dataset

STRUCT = """schema = "structure@1.0.0"
[areas.rec]
path = "records"
role = "record"
mutability = "mutable"
[areas.out]
path = "out"
role = "data"
mutability = "sealed"
"""


def _unlock(root: Path) -> None:
    for cur, _dirs, files in os.walk(root):
        os.chmod(cur, 0o755)
        for f in files:
            q = os.path.join(cur, f)
            if not os.path.islink(q):
                os.chmod(q, 0o644)


@pytest.fixture
def proj(tmp_path, monkeypatch):
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
    monkeypatch.chdir(root)
    yield root
    _unlock(tmp_path)


def _seal(root: Path, **kw) -> str:
    p = load_project(root)
    return seal_dataset(str(root), p.structure, "out/ds", local=p.local, **kw)["snapshot"]


def _diff(root: Path, **kw):
    return diff_dataset(str(root), load_project(root).structure, "out/ds", **kw)


def _edit(root: Path, fn) -> None:
    _unlock(root)
    fn(root / "out" / "ds")


def test_first_version_against_empty(proj):
    s1 = _seal(proj)
    d = _diff(proj)
    assert d["diff_version"] == 1 and d["from"] is None and d["to"]["snapshot"] == s1
    assert d["counts"]["added"] == 3 and d["counts"]["unchanged"] == 0
    assert [e["path"] for e in d["added"]] == ["a.txt", "sub/b.txt", "sub/c.txt"]
    assert d["bytes"]["added"] == 5 + 4 + 5


@pytest.mark.parametrize("opts", [{}, {"stream": True}, {"layout": "tree"}])
def test_added_removed_changed_default_previous_vs_head(proj, opts):
    s1 = _seal(proj, **opts)

    def mutate(ds):
        (ds / "a.txt").write_text("ALPHA!")
        (ds / "sub" / "b.txt").unlink()
        (ds / "new.txt").write_text("new")

    _edit(proj, mutate)
    s2 = _seal(proj, **opts)
    d = _diff(proj)
    assert d["from"]["snapshot"] == s1 and d["to"]["snapshot"] == s2 and d["relation"] == "descendant"
    assert d["counts"] == {"added": 1, "removed": 1, "changed": 1, "moved": 0, "unchanged": 1, "absent": 0,
                           "unreadable": 0}
    assert d["changed"][0]["from"]["size"] == 5 and d["changed"][0]["to"]["size"] == 6
    assert d["removed"][0]["path"] == "sub/b.txt" and d["added"][0]["path"] == "new.txt"
    back = _diff(proj, from_digest=s2, to_digest=s1)
    assert back["relation"] == "ancestor" and back["counts"]["added"] == 1 and back["counts"]["removed"] == 1


def test_moved(proj):
    _seal(proj)
    _edit(proj, lambda ds: os.rename(ds / "sub" / "b.txt", ds / "moved.txt"))
    _seal(proj)
    d = _diff(proj)
    assert d["counts"]["moved"] == 1 and d["counts"]["added"] == 0 and d["counts"]["removed"] == 0
    assert d["moved"][0]["from_path"] == "sub/b.txt" and d["moved"][0]["to_path"] == "moved.txt"


def test_duplicates_pair_one_to_one_and_same_content(proj):
    old = {"x": {"digest": "sha256:" + "a" * 64, "size": 3}, "keep": {"digest": "sha256:" + "b" * 64, "size": 2}}
    dig = old["x"]["digest"]
    new = {"y": {"digest": dig, "size": 3}, "z": {"digest": dig, "size": 3},
           "keep": old["keep"], "w": {"digest": old["keep"]["digest"], "size": 2}}
    r = classify(old, new)
    assert r["moved"] == [{"from_path": "x", "to_path": "y", "size": 3, "digest": dig}]
    assert [a["path"] for a in r["added"]] == ["w", "z"]
    assert {a["path"]: a["same_content_as"] for a in r["added"]} == {"w": "keep", "z": "x"}
    assert r["removed"] == [] and r["unchanged"] == 1


def test_empty_files_never_paired(proj):
    empty = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    r = classify({"a": {"digest": empty, "size": 0}}, {"b": {"digest": empty, "size": 0}})
    assert r["moved"] == [] and len(r["added"]) == 1 and len(r["removed"]) == 1
    assert r["added"][0]["same_content_as"] is None


def test_empty_file_in_snapshots(proj):
    _seal(proj)
    _edit(proj, lambda ds: (ds / "empty").write_text(""))
    _seal(proj)
    d = _diff(proj)
    assert d["counts"]["added"] == 1 and d["added"][0]["size"] == 0 and d["added"][0]["same_content_as"] is None


def test_selection(proj):
    _seal(proj)
    _edit(proj, lambda ds: ((ds / "n.txt").write_text("n"), (ds / "sub" / "n.md").write_text("m")))
    _seal(proj)
    assert _diff(proj, paths=["sub"])["counts"]["added"] == 1
    assert _diff(proj, include=["*.txt"])["added"][0]["path"] == "n.txt"
    d = _diff(proj, exclude=["sub/*"])
    assert [e["path"] for e in d["added"]] == ["n.txt"] and d["counts"]["unchanged"] == 1


def test_works_without_content(proj):
    _seal(proj)
    _edit(proj, lambda ds: (ds / "a.txt").write_text("changed"))
    _seal(proj)
    _unlock(proj)
    shutil.rmtree(proj / "out" / "ds")
    assert _diff(proj)["counts"]["changed"] == 1


def test_errors(proj):
    s1 = _seal(proj)
    with pytest.raises(DiffError):
        _diff(proj, from_digest="sha256:" + "0" * 64)
    with pytest.raises(DiffError):
        diff_dataset(str(proj), load_project(proj).structure, "out/none")
    _edit(proj, lambda ds: (ds / "q").write_text("q"))
    _seal(proj)
    with pytest.raises(DiffError):
        diff_dataset(str(proj), load_project(proj).structure, "out/ds", from_digest=s1, to_digest="sha256:" + "1" * 64)


def test_cli_text_json_and_exit_codes(proj, capsys):
    _seal(proj)
    assert main(["diff", "out/ds", "--json"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert j["diff_version"] == 1 and j["counts"]["added"] == 3 and j["from"] is None
    assert main(["diff", "out/ds"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("diff out/ds null -> sha256:") and "A a.txt" in out
    assert "3 added, 0 removed, 0 changed, 0 moved, 0 unchanged; +14 B, -0 B" in out.splitlines()[-1]
    assert main(["diff", "out/ds", "--stat"]) == 1
    assert len(capsys.readouterr().out.splitlines()) == 2
    s1 = j["to"]["snapshot"]
    assert main(["diff", "out/ds", "--from", s1, "--to", s1]) == 0
    capsys.readouterr()
    assert main(["diff", "out/nope"]) == 3
    assert main(["diff"]) == 3


def test_size_units():
    from stradler.diff import _size
    assert [_size(n) for n in (0, 1023, 1024, 1536, 1 << 20, 3 << 30)] == [
        "0 B", "1023 B", "1.0 KiB", "1.5 KiB", "1.0 MiB", "3.0 GiB"]


def _working(root, **kw):
    return _diff(root, to_digest="working", **kw)


def test_working_unchanged_then_edits(proj):
    s1 = _seal(proj)
    d = _working(proj)
    assert d["from"]["snapshot"] == s1 and d["to"] == {"working": True, "depth": "sha256"}
    assert d["relation"] is None and d["counts"]["unchanged"] == 3
    assert main(["diff", "out/ds", "--to", "working"]) == 0
    _edit(proj, lambda ds: ((ds / "a.txt").write_text("ALPHA"), (ds / "new.txt").write_text("n"),
                            os.rename(ds / "sub" / "b.txt", ds / "moved.txt"), (ds / "sub" / "c.txt").unlink()))
    d = _working(proj)
    assert d["counts"] == {"added": 1, "removed": 1, "changed": 1, "moved": 1, "unchanged": 0, "absent": 0,
                           "unreadable": 0}
    assert d["changed"][0]["to"]["digest"].startswith("sha256:")
    assert d["moved"][0]["to_path"] == "moved.txt" and d["removed"][0]["path"] == "sub/c.txt"


def test_working_depth_size(proj):
    _seal(proj)
    _edit(proj, lambda ds: ((ds / "a.txt").write_text("ALPHA"), (ds / "sub" / "b.txt").write_text("bet"),
                            (ds / "new.txt").write_text("n"), (ds / "sub" / "c.txt").unlink()))
    d = _working(proj, depth="size")
    assert d["to"]["depth"] == "size"
    assert d["counts"]["unchanged"] == 1 and d["counts"]["changed"] == 1  # a.txt same size, b.txt differs
    assert d["changed"][0]["path"] == "sub/b.txt" and d["changed"][0]["to"] == {"size": 3, "digest": None}
    assert d["added"][0]["digest"] is None and d["counts"]["moved"] == 0
    with pytest.raises(DiffError):
        _working(proj, depth="md5")


def test_working_unreadable_symlink_and_text(proj, capsys):
    _seal(proj)
    _edit(proj, lambda ds: ((ds / "sub" / "b.txt").unlink(), os.symlink("a.txt", ds / "sub" / "b.txt")))
    d = _working(proj)
    assert d["unreadable"] == [{"path": "sub/b.txt", "reason": "symbolic link"}]
    assert d["counts"]["removed"] == 0 and d["counts"]["unchanged"] == 2
    assert main(["diff", "out/ds", "--to", "working"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("-> working (depth sha256)")
    assert "! sub/b.txt symbolic link" in lines
    assert lines[-1] == "0 added, 0 removed, 0 changed, 0 moved, 2 unchanged, 0 absent, 1 unreadable; +0 B, -0 B"


def test_working_missing_file_is_removed_in_present_area(proj):
    _seal(proj)
    _edit(proj, lambda ds: (ds / "a.txt").unlink())
    d = _working(proj)
    assert d["counts"]["removed"] == 1 and d["counts"]["absent"] == 0


def test_working_absent_in_on_demand_area(proj):
    (proj / ".stradler" / "structure.toml").write_text(
        STRUCT.replace('mutability = "sealed"', 'mutability = "sealed"\ncontent = "on_demand"'))
    _seal(proj)
    _edit(proj, lambda ds: (ds / "a.txt").unlink())
    d = _working(proj)
    assert d["counts"]["absent"] == 1 and d["counts"]["removed"] == 0 and d["absent"][0]["size"] == 5
    assert d["absent"][0]["digest"].startswith("sha256:")
    assert main(["diff", "out/ds", "--to", "working", "--depth", "size", "--stat"]) == 0


def test_working_explicit_from_and_selection(proj):
    s1 = _seal(proj)
    _edit(proj, lambda ds: (ds / "n.txt").write_text("n"))
    _seal(proj)
    d = _working(proj, from_digest=s1, include=["*.txt"], paths=["sub"])
    assert d["from"]["snapshot"] == s1 and d["counts"]["added"] == 0 and d["counts"]["unchanged"] == 2
