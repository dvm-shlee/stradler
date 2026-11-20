"""``stradler adopt``: in place, copy mode, and provenance."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from stradler import adopt, snapshot
from stradler.adopt import AdoptError, adopt_in_place, adopt_plan, propose
from stradler.cli import main
from stradler.init import init_project
from stradler.project import load_project
from stradler.snapshot import SnapshotError, file_digest, read_snapshot, write_snapshot
from stradler.validate import validate_project

TEMPLATE = Path(__file__).parent / "data" / "template.yaml"


def make_tree(root: Path) -> Path:
    (root / "raw" / "sub").mkdir(parents=True)
    (root / "raw" / "a.txt").write_bytes(b"hello\n")
    (root / "raw" / "sub" / "b.bin").write_bytes(b"x" * 10)
    (root / "notes").mkdir()
    (root / "notes" / "n.md").write_text("n")
    (root / ".hidden").mkdir()
    (root / "top.txt").write_text("t")
    return root


def tree_state(root: Path) -> list:
    out = []
    for p in sorted(root.rglob("*")):
        out.append((str(p.relative_to(root)), p.is_dir(), file_digest(p)[0] if p.is_file() else None))
    return out


# ---- in place ----


def test_dry_run_changes_nothing(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    before = tree_state(root)
    assert main(["adopt", "--dry-run", "--json", str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert set(out["structure"]["areas"]) == {"raw", "notes"}
    for a in out["structure"]["areas"].values():
        assert (a["role"], a["mutability"], a["grade"]) == ("input", "immutable", "private")
    assert tree_state(root) == before


def test_in_place_writes_only_project_files(tmp_path):
    root = make_tree(tmp_path / "d")
    before = tree_state(root)
    assert adopt_in_place(root) == root.resolve()
    after = tree_state(root)
    new = [e for e in after if e not in before]
    assert all(e[0] == "stradler.json" or e[0].startswith(".stradler") for e in new)
    assert [e for e in before if e not in after] == []
    problems = validate_project(load_project(root))
    assert problems == []
    structure = tomllib.loads((root / ".stradler" / "structure.toml").read_text())
    assert structure["areas"]["raw"]["path"] == "raw"


def test_refuses_existing_project(tmp_path):
    root = init_project(TEMPLATE, tmp_path / "p")
    with pytest.raises(AdoptError, match="already"):
        adopt_in_place(root)
    with pytest.raises(AdoptError):
        propose(root)


def test_empty_directory_refused_and_left_alone(tmp_path):
    (tmp_path / "e").mkdir()
    with pytest.raises(AdoptError):
        adopt_in_place(tmp_path / "e")
    assert list((tmp_path / "e").iterdir()) == []


def test_template_overrides_defaults(tmp_path):
    tpl = tmp_path / "t.json"
    tpl.write_text(json.dumps({
        "name": "t", "version": "0.1.0",
        "areas": {"src": {"role": "input", "path": "raw", "mutability": "immutable", "grade": "work"}},
    }))
    root = make_tree(tmp_path / "d")
    prop = propose(root, tpl)
    areas = prop["structure"]["areas"]
    assert areas["src"]["grade"] == "work" and areas["notes"]["grade"] == "private" and "raw" not in areas
    adopt_in_place(root, tpl)
    assert (root / ".stradler" / "template.json").is_file()
    assert validate_project(load_project(root)) == []


def test_cli_in_place(tmp_path, capsys):
    root = make_tree(tmp_path / "d")
    assert main(["adopt", "--json", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["adopt", "--json", str(root)]) == 1


# ---- copy mode ----


def plan_for(tmp_path: Path, files: dict[str, bytes], prefix: str = "raw") -> tuple[Path, list]:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    plan = []
    for name, data in files.items():
        (src / name).write_bytes(data)
        d, n = file_digest(src / name)
        plan.append({"source": str(src / name), "dest": f"{prefix}/{name}", "size": n, "digest": d})
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(plan))
    return f, plan


@pytest.fixture
def proj(tmp_path):
    return init_project(TEMPLATE, tmp_path / "proj")


def test_copy_verifies_and_summarises(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha", "b": b"beta" * 100})
    src_state = tree_state(tmp_path / "src")
    s = adopt_plan(f, proj)
    assert (s["files"], s["copied"], s["skipped"], s["bytes_copied"]) == (2, 2, 0, 5 + 400)
    for e in plan:
        assert file_digest(proj / e["dest"])[0] == e["digest"]
    assert tree_state(tmp_path / "src") == src_state
    rec = list((proj / ".stradler" / "adopt").glob("summary-*.json"))
    assert len(rec) == 1
    text = rec[0].read_text()
    assert "src" not in text and "raw" not in text and json.loads(text)["copied"] == 2


def test_copy_refuses_digest_mismatch(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    plan[0]["digest"] = "sha256:" + "0" * 64
    f.write_text(json.dumps(plan))
    with pytest.raises(AdoptError, match="mismatch"):
        adopt_plan(f, proj)
    assert not (proj / plan[0]["dest"]).exists()
    assert not list(proj.rglob("*.stradler-part"))


def test_copy_refuses_size_mismatch(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    plan[0]["size"] += 1
    f.write_text(json.dumps(plan))
    with pytest.raises(AdoptError, match="mismatch"):
        adopt_plan(f, proj)
    assert not (proj / plan[0]["dest"]).exists()


def test_copy_refuses_dest_with_other_content_before_copying(tmp_path, proj):
    f, _plan = plan_for(tmp_path, {"a": b"alpha", "b": b"beta"})
    (proj / "raw").mkdir()
    (proj / "raw" / "b").write_bytes(b"other")
    with pytest.raises(AdoptError, match="other content"):
        adopt_plan(f, proj)
    assert not (proj / "raw" / "a").exists()
    assert (proj / "raw" / "b").read_bytes() == b"other"


def test_copy_resumes_after_interruption(tmp_path, proj, monkeypatch):
    files = {"a": b"alpha", "b": b"beta", "c": b"gamma"}
    f, plan = plan_for(tmp_path, files)
    real = adopt._copy_one
    calls = []

    def flaky(source, tmp):
        calls.append(source)
        if len(calls) == 2:
            tmp.write_bytes(b"par")  # partial temporary file, as a killed copy leaves
            raise KeyboardInterrupt
        real(source, tmp)

    monkeypatch.setattr(adopt, "_copy_one", flaky)
    with pytest.raises(KeyboardInterrupt):
        adopt_plan(f, proj)
    assert (proj / "raw" / "a").is_file() and not (proj / "raw" / "b").exists()
    monkeypatch.setattr(adopt, "_copy_one", real)
    s = adopt_plan(f, proj)
    assert (s["copied"], s["skipped"]) == (2, 1)
    for e in plan:
        assert file_digest(proj / e["dest"])[0] == e["digest"]
    assert not list(proj.rglob("*.stradler-part"))
    again = adopt_plan(f, proj)
    assert (again["copied"], again["skipped"]) == (0, 3)


@pytest.mark.parametrize("dest", ["../x", "/abs", ".stradler/x", "stradler.json", "a//b", "a/./b"])
def test_plan_refuses_bad_dest(tmp_path, proj, dest):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    plan[0]["dest"] = dest
    f.write_text(json.dumps(plan))
    with pytest.raises(AdoptError):
        adopt_plan(f, proj)


def test_plan_shape_refused(tmp_path, proj):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([{"source": "x", "dest": "y"}]))
    with pytest.raises(AdoptError):
        adopt_plan(f, proj)
    f.write_text("{}")
    with pytest.raises(AdoptError):
        adopt_plan(f, proj)


def test_cli_plan(tmp_path, proj, capsys):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    assert main(["adopt", "--plan", str(f), "--json", str(proj)]) == 0
    assert json.loads(capsys.readouterr().out)["copied"] == 1
    f.write_text(json.dumps([{"source": "/nonexistent/zz", "dest": "raw/z", "size": 1, "digest": "sha256:" + "1" * 64}]))
    assert main(["adopt", "--plan", str(f), "--json", str(proj)]) == 1


# ---- provenance ----


def test_provenance_copied_into_snapshot(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "a.txt").write_bytes(b"hello\n")
    prov = {"origin": "src-1", "checked_at": "2026-11-15T00:00:00Z", "metadata": {"k": [1, None]}}
    doc = write_snapshot(tmp_path, "ds", provenance=prov)
    assert doc["provenance"] == prov
    stored = read_snapshot(tmp_path / "records" / "snapshots" / "v2" / (doc["digest"][7:] + ".json"))
    assert stored["provenance"] == prov
    plain = write_snapshot(tmp_path, "ds")  # same content: same id; provenance is not digested
    assert plain["digest"] == doc["digest"]


@pytest.mark.parametrize("prov", [
    {"origin": 1}, {"checked_at": "yesterday"}, {"metadata": []}, {"metadata": {"f": 1.5}},
])
def test_provenance_shape_refused(tmp_path, prov):
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "a.txt").write_bytes(b"x")
    with pytest.raises(SnapshotError):
        write_snapshot(tmp_path, "ds", provenance=prov)
    assert not (tmp_path / "records").exists() or not list((tmp_path / "records").rglob("*.json"))
    assert snapshot.check_provenance({"origin": "x", "extra": 1}) is None
