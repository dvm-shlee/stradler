"""``stradler adopt``: in place, copy mode, and provenance."""

from __future__ import annotations

import json
import os
import signal
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

    def flaky(source, tmp, *a):
        calls.append(source)
        if len(calls) == 2:
            tmp.write_bytes(b"par")  # partial temporary file, as a killed copy leaves
            raise KeyboardInterrupt
        real(source, tmp, *a)

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


# ---- copy engine: times, rate limit, progress, clean stop ----


def test_copy_keeps_mtime_and_atime(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    os.utime(plan[0]["source"], ns=(1_000_000_000_000_000_000, 1_704_153_600_000_000_000))
    adopt_plan(f, proj)
    st = (proj / "raw" / "a").stat()
    assert st.st_mtime_ns == 1_704_153_600_000_000_000
    assert st.st_atime_ns == 1_000_000_000_000_000_000


def test_no_keep_times_uses_copy_time(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    os.utime(plan[0]["source"], ns=(1_000_000_000_000_000_000, 1_704_153_600_000_000_000))
    adopt_plan(f, proj, keep_times=False)
    assert (proj / "raw" / "a").stat().st_mtime_ns != 1_704_153_600_000_000_000


def test_times_set_before_rename(tmp_path, proj, monkeypatch):
    f, plan = plan_for(tmp_path, {"a": b"alpha"})
    os.utime(plan[0]["source"], ns=(10**18, 1_704_153_600_000_000_000))
    seen = []
    real = os.replace

    def spy(a, b):
        seen.append(Path(a).stat().st_mtime_ns)
        real(a, b)

    monkeypatch.setattr(adopt.os, "replace", spy)
    adopt_plan(f, proj)
    assert seen == [1_704_153_600_000_000_000]


@pytest.mark.parametrize("text,n", [("100", 100), ("1K", 1024), ("2m", 2 << 20), ("1.5G", 3 << 29)])
def test_parse_rate(text, n):
    assert adopt.parse_rate(text) == n


@pytest.mark.parametrize("text", ["", "0", "-5", "abc", "10T", "1KB"])
def test_parse_rate_refused(text):
    with pytest.raises(AdoptError):
        adopt.parse_rate(text)


def test_bwlimit_paces_with_fake_clock(tmp_path, proj, monkeypatch):
    f, _ = plan_for(tmp_path, {"a": b"x" * 100})
    now = [0.0]
    sleeps = []
    monkeypatch.setattr(adopt, "_CHUNK", 10)
    monkeypatch.setattr(adopt, "_clock", lambda: now[0])
    monkeypatch.setattr(adopt, "_sleep", lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)))
    adopt_plan(f, proj, bwlimit=50)
    assert now[0] == pytest.approx(2.0)
    assert sum(sleeps) == pytest.approx(2.0)


def test_progress_lines_and_final(tmp_path, proj, monkeypatch):
    f, _ = plan_for(tmp_path, {"a": b"x" * 40, "b": b"y" * 20})
    now = [0.0]
    monkeypatch.setattr(adopt, "_CHUNK", 10)
    monkeypatch.setattr(adopt, "_clock", lambda: (now.__setitem__(0, now[0] + 1.0), now[0])[1])
    got = []
    adopt_plan(f, proj, progress=got.append, progress_interval=3)
    assert len(got) >= 3
    assert got[-1] == {"files_done": 2, "files_total": 2, "bytes_done": 60, "bytes_total": 60}
    assert all(set(g) == {"files_done", "files_total", "bytes_done", "bytes_total"} for g in got)
    assert [g["bytes_done"] for g in got] == sorted(g["bytes_done"] for g in got)


def test_cli_progress_on_stderr_only_with_json(tmp_path, proj, capsys):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    assert main(["adopt", "--plan", str(f), "--json", str(proj)]) == 0
    cap = capsys.readouterr()
    last = json.loads(cap.err.strip().splitlines()[-1])
    assert last["files_done"] == 1 and last["bytes_total"] == 5
    assert json.loads(cap.out)["interrupted"] is False
    f2, _ = plan_for(tmp_path, {"b": b"beta"}, prefix="raw2")
    assert main(["adopt", "--plan", str(f2), str(proj)]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("sig,code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_clean_stop_on_signal_then_resume(tmp_path, proj, monkeypatch, capsys, sig, code):
    files = {"a": b"alpha", "b": b"beta", "c": b"gamma"}
    f, plan = plan_for(tmp_path, files)
    real = adopt._copy_one
    calls = []

    def killer(source, tmp, *a):
        calls.append(source)
        if len(calls) == 2:
            os.kill(os.getpid(), sig)
        real(source, tmp, *a)

    monkeypatch.setattr(adopt, "_copy_one", killer)
    assert main(["adopt", "--plan", str(f), "--json", str(proj)]) == code
    out = json.loads(capsys.readouterr().out)
    assert out["interrupted"] is True and out["ok"] is False and out["copied"] == 1
    assert not list(proj.rglob("*.stradler-part"))
    assert (proj / "raw" / "a").is_file() and not (proj / "raw" / "b").exists()
    summaries = list((proj / ".stradler" / "adopt").glob("summary-*.json"))
    assert json.loads(summaries[0].read_text())["interrupted"] is True
    monkeypatch.setattr(adopt, "_copy_one", real)
    s = adopt_plan(f, proj)
    assert (s["copied"], s["skipped"], s["interrupted"]) == (2, 1, False)


def test_signal_handlers_restored(tmp_path, proj):
    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    adopt_plan(f, proj)
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


def test_summary_stays_path_free(tmp_path, proj):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    s = adopt_plan(f, proj)
    text = json.dumps(s)
    assert str(tmp_path) not in text and "raw/" not in text
