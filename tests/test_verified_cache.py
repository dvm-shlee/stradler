"""Machine-local verified-digest cache: adopt writes it, snapshot and seal may reuse it."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from stradler import snapshot, verified
from stradler.adopt import adopt_plan
from stradler.cli import main
from stradler.init import init_project
from stradler.project import load_project
from stradler.seal import seal_dataset
from stradler.snapshot import file_digest, write_snapshot

TEMPLATE = Path(__file__).parent / "data" / "template.yaml"
CACHE = Path(".stradler") / "local" / "verified.jsonl"


@pytest.fixture
def proj(tmp_path):
    yield init_project(TEMPLATE, tmp_path / "proj")
    for p in (tmp_path / "proj").rglob("*"):
        if not p.is_symlink():
            os.chmod(p, p.stat().st_mode | 0o700)


def plan_for(tmp_path, files, prefix="code"):
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


def lines(proj):
    return [json.loads(x) for x in (Path(proj) / CACHE).read_text().splitlines()]


def test_adopt_writes_cache_and_gitignore(tmp_path, proj):
    f, plan = plan_for(tmp_path, {"a": b"alpha", "b": b"beta"})
    adopt_plan(f, proj)
    got = {x["dest"]: x for x in lines(proj)}
    assert set(got) == {"code/a", "code/b"}
    for e in plan:
        x = got[e["dest"]]
        assert x["verified_version"] == 1 and x["size"] == e["size"] and x["sha256"] == e["digest"]
        assert x["mtime_ns"] == os.lstat(Path(proj) / e["dest"]).st_mtime_ns
        assert len(x["verified_at"]) == 20 and x["verified_at"].endswith("Z")
    ign = (Path(proj) / ".stradler" / ".gitignore").read_text().splitlines()
    assert "local/" in ign and "local.toml" in ign


def test_adopt_adds_missing_ignore_line_once(tmp_path, proj):
    ig = Path(proj) / ".stradler" / ".gitignore"
    ig.write_text("local.toml")
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    adopt_plan(f, proj)
    adopt_plan(f, proj)
    assert ig.read_text().splitlines() == ["local.toml", "local/"]


def test_already_matching_files_are_recorded_and_flag_turns_off(tmp_path, proj):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    adopt_plan(f, proj, record_verified=False)
    assert not (Path(proj) / CACHE).exists()
    s = adopt_plan(f, proj)
    assert s["skipped"] == 1
    assert [x["dest"] for x in lines(proj)] == ["code/a"]


def test_cli_no_record_verified(tmp_path, proj):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    assert main(["adopt", "--plan", str(f), "--no-record-verified", str(proj)]) == 0
    assert not (Path(proj) / CACHE).exists()


def test_dry_run_writes_no_cache(tmp_path, proj):
    f, _ = plan_for(tmp_path, {"a": b"alpha"})
    main(["adopt", "--plan", str(f), "--dry-run", "--json", str(proj)])
    assert not (Path(proj) / CACHE).exists()


def test_load_ignores_malformed_lines_and_latest_wins(tmp_path):
    root = tmp_path
    d = "sha256:" + "a" * 64
    verified.record(root, "x/y", 1, 2, d)
    verified.record(root, "x/y", 3, 4, d)
    with open(root / CACHE, "a") as fh:
        fh.write("not json\n{}\n" + json.dumps({"verified_version": 1, "dest": "z", "size": "1", "mtime_ns": 1,
                                                 "sha256": d}) + "\n")
    entries, bad = verified.load(root)
    assert entries == {"x/y": (3, 4, d)} and bad == 3


def _dataset(root, text="alpha"):
    p = Path(root) / "out" / "ds"
    p.mkdir(parents=True, exist_ok=True)
    (p / "a.txt").write_text(text)
    return p


def _seed(root, rel, size, mtime_ns, digest):
    verified.record(root, rel, size, mtime_ns, digest)


def test_snapshot_reuse_on_match_uses_cached_digest(tmp_path, proj, monkeypatch):
    ds = _dataset(proj)
    st = os.lstat(ds / "a.txt")
    fake = "sha256:" + "b" * 64
    _seed(proj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, fake)
    reuse = verified.Reuse(proj)
    doc = write_snapshot(proj, "out/ds", load_project(proj).structure, reuse=reuse)
    assert doc["entries"][0]["digest"] == fake and reuse.count == 1


def test_snapshot_no_reuse_without_flag_or_on_mismatch(tmp_path, proj):
    ds = _dataset(proj)
    st = os.lstat(ds / "a.txt")
    real = file_digest(ds / "a.txt")[0]
    fake = "sha256:" + "b" * 64
    _seed(proj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, fake)
    s = load_project(proj).structure
    assert write_snapshot(proj, "out/ds", s)["entries"][0]["digest"] == real
    # mtime mismatch
    verified_path = Path(proj) / CACHE
    verified_path.unlink()
    _seed(proj, "out/ds/a.txt", st.st_size, st.st_mtime_ns + 1, fake)
    r = verified.Reuse(proj)
    assert write_snapshot(proj, "out/ds", s, reuse=r)["entries"][0]["digest"] == real and r.count == 0
    # size mismatch
    verified_path.unlink()
    _seed(proj, "out/ds/a.txt", st.st_size + 1, st.st_mtime_ns, fake)
    r = verified.Reuse(proj)
    assert write_snapshot(proj, "out/ds", s, reuse=r)["entries"][0]["digest"] == real and r.count == 0


def test_scope_does_not_leak(tmp_path, proj):
    ds = _dataset(proj)
    st = os.lstat(ds / "a.txt")
    _seed(proj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, "sha256:" + "b" * 64)
    write_snapshot(proj, "out/ds", load_project(proj).structure, reuse=verified.Reuse(proj))
    assert verified.current() is None
    assert file_digest(ds / "a.txt")[0] != "sha256:" + "b" * 64


def test_tree_layout_reuse(tmp_path, proj):
    ds = _dataset(proj)
    st = os.lstat(ds / "a.txt")
    fake = "sha256:" + "c" * 64
    _seed(proj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, fake)
    r = verified.Reuse(proj)
    tree = snapshot.build_tree  # build under scope
    with verified.reuse_scope(r):
        t = tree(ds)
    assert r.count == 1 and any(c["digest"] == fake for ch in t.nodes.values() for c in ch)


STRUCT = """schema = "structure@1.0.0"\n[areas.rec]\npath = "records"\nrole = "record"\nmutability = "mutable"\n[areas.out]\npath = "out"\nrole = "data"\nmutability = "sealed"\n"""


@pytest.fixture
def sealproj(tmp_path):
    root = tmp_path / "sp"
    (root / ".stradler").mkdir(parents=True)
    (root / "stradler.json").write_text(json.dumps({"marker": "stradler-project", "project_id": "t"}))
    (root / ".stradler" / "config.toml").write_text('schema = "config@1.0.0"\n')
    (root / ".stradler" / "structure.toml").write_text(STRUCT)
    _dataset(root)
    yield root
    for p in root.rglob("*"):
        if not p.is_symlink():
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o700)


def _seal(root, **kw):
    p = load_project(root)
    return seal_dataset(str(root), p.structure, "out/ds", local=p.local, **kw)


def test_seal_reuse_reports_count_and_off_by_default(sealproj):
    ds = sealproj / "out" / "ds" / "a.txt"
    st = os.lstat(ds)
    _seed(sealproj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, file_digest(ds)[0])
    (sealproj / CACHE).open("a").write("garbage\n")
    out = _seal(sealproj, reuse_verified=True)
    assert out["reused_digests"] == 1 and out["cache_warnings"] == 1 and out["result"] == "match"


def test_seal_without_flag_has_no_reuse_keys(sealproj):
    out = _seal(sealproj)
    assert "reused_digests" not in out


def test_seal_cli_flag(sealproj, capsys):
    ds = sealproj / "out" / "ds" / "a.txt"
    st = os.lstat(ds)
    _seed(sealproj, "out/ds/a.txt", st.st_size, st.st_mtime_ns, file_digest(ds)[0])
    code = main(["seal", "--json", "--reuse-verified", str(sealproj / "out" / "ds")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["reused_digests"] == 1


def test_init_gitignore_lists_local(tmp_path):
    p = init_project(TEMPLATE, tmp_path / "p")
    assert "local/" in (Path(p) / ".stradler" / ".gitignore").read_text().splitlines()
