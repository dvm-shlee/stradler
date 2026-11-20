"""Snapshot format version 2 (docs/SNAPSHOTS.md): digests, writer, reader and conformance cases."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from stradler import snapshot
from stradler.snapshot import (
    SnapshotError,
    build_tree,
    file_digest,
    read_snapshot,
    write_snapshot,
)
from stradler.workflow import canonical_json, digest_of

CONF = Path(__file__).parent / "conformance" / "snapshot"
STRUCT = {"areas": {"rec": {"path": "records", "role": "record"}}}

# SNAPSHOTS.md section 5.4
A_DIGEST = "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
B_DIGEST = "sha256:2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
V1 = "sha256:5d10b0c2f98dac43fd194754aaa76463d4a89a58827fe7915702d3cde0c659ca"
FLAT = "sha256:8d49f9e2ac5d0091f4191f6acb630afb794cb14ac7388e48f150a2a3d9791288"
SUB = "sha256:1b481426fe28c8e29ea5d61630cf07b25b113788016cc0dda4927cec85056213"
ROOT = "sha256:716f5a1fe2277248e4ca3b3da8af3f74fd8db3cb09c50fd5b1b74cc76c5acb3e"
TREE = "sha256:fbdc140cfca370afc606e66f6feb4401fa406def1dae2a9bfa0bd3192424c6b3"


# ---- independent reference: plain hashlib and json, none of the module under test ----


def _cj(v) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _dg(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _content(f: dict) -> bytes:
    return f["content"].encode("utf-8") if f["encoding"] == "utf-8" else base64.b64decode(f["content"])


def reference(case: dict) -> dict:
    """Compute the expected values of a conformance input from the rules of the document."""
    files = {f["path"]: _content(f) for f in case["files"]}
    entries = [
        {"path": p, "digest": _dg(files[p]), "size": len(files[p])}
        for p in sorted(files, key=lambda s: s.encode("utf-8"))
    ]
    total = sum(e["size"] for e in entries)

    def node(prefix: str):
        kids = {}
        for p in files:
            if prefix and not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            kids.setdefault(rest.split("/")[0], []).append(p)
        recs, size = [], 0
        for name in sorted(kids, key=lambda s: s.encode("utf-8")):
            if kids[name] == [prefix + name]:
                recs.append({"name": name, "kind": "file", "digest": _dg(files[prefix + name]), "size": len(files[prefix + name])})
            else:
                d, s = node(prefix + name + "/")
                recs.append({"name": name, "kind": "dir", "digest": d, "size": s})
            size += recs[-1]["size"]
        return _dg(_cj(recs)), size

    root, _ = node("")
    return {
        "v1": {"digest": _dg(_cj(entries)), "file_count": len(entries), "total_size": total},
        "flat": {
            "digest": _dg(_cj({"algorithm": "sha256", "entries": entries, "layout": "flat"})),
            "file_count": len(entries),
            "total_size": total,
        },
        "tree": {
            "digest": _dg(_cj({"algorithm": "sha256", "layout": "tree", "root": root})),
            "root": root,
            "file_count": len(entries),
            "total_size": total,
        },
    }


def materialize(case: dict, dest: Path) -> Path:
    dest.mkdir(parents=True)
    for f in case["files"]:
        p = dest / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_content(f))
    for d in case["empty_dirs"]:
        (dest / d).mkdir(parents=True, exist_ok=True)
    return dest


def _example(root: Path, name: str = "ds") -> None:
    d = root / name
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"hello\n")
    (d / "sub" / "b.bin").write_bytes(b"x")


# ---- digests ----


def test_documented_example_digests(tmp_path):
    _example(tmp_path)
    assert file_digest(tmp_path / "ds" / "a.txt") == (A_DIGEST, 6)
    entries = list(snapshot.iter_entries(tmp_path / "ds"))
    assert [e["path"] for e in entries] == ["a.txt", "sub/b.bin"]
    assert entries[1]["digest"] == B_DIGEST
    assert digest_of(entries) == V1
    assert snapshot.flat_content_digest(entries) == FLAT
    tree = build_tree(tmp_path / "ds")
    assert tree.node_digest("sub") == SUB
    assert tree.node_digest("") == tree.root == ROOT
    assert tree.content_digest == TREE
    assert (tree.file_count, tree.total_size) == (2, 7)


def test_stream_digest_equals_inline(tmp_path):
    _example(tmp_path)
    entries = list(snapshot.iter_entries(tmp_path / "ds"))
    lines = (canonical_json(e) for e in entries)
    assert snapshot.stream_content_digest(lines) == FLAT
    assert snapshot.stream_content_digest(canonical_json(e).encode() for e in entries) == FLAT
    assert snapshot.stream_content_digest([]) != FLAT


def test_node_digest_is_digest_of_child_array():
    kids = [{"digest": B_DIGEST, "kind": "file", "name": "b.bin", "size": 1}]
    assert snapshot.node_digest(kids) == SUB
    assert snapshot.tree_content_digest(ROOT) == TREE


def test_digest_sorts_by_utf8_bytes(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    for n in ("\U0001f600.txt", "～.txt", "b"):
        (d / n).write_bytes(b"1")
    assert [e["path"] for e in snapshot.iter_entries(d)] == ["b", "～.txt", "\U0001f600.txt"]


# ---- writer ----


def test_write_flat_inline_stream_and_tree(tmp_path):
    _example(tmp_path)
    inline = write_snapshot(tmp_path, "ds", STRUCT, created="2026-11-15T00:00:00Z")
    assert inline["digest"] == FLAT and inline["entries"][0]["path"] == "a.txt"
    assert (inline["file_count"], inline["total_size"], inline["previous"]) == (2, 7, None)
    folder = tmp_path / "records" / "snapshots" / "v2"
    assert (folder / (FLAT.split(":")[1] + ".json")).is_file()
    r2 = tmp_path / "r2"
    r2.mkdir()
    _example(r2, "ds2")
    st = write_snapshot(r2, "ds2", STRUCT, stream=True)
    assert st["digest"] == FLAT and "entries" not in st
    folder2 = r2 / "records" / "snapshots" / "v2"
    sfile = folder2 / st["entries_stream"]
    assert sfile.name == FLAT.split(":")[1] + ".entries.jsonl"
    assert sfile.read_bytes().count(b"\n") == 2
    _example(tmp_path, "ds3")
    tr = write_snapshot(tmp_path, "ds3", STRUCT, layout="tree")
    assert tr["digest"] == TREE and tr["root"] == ROOT
    node = folder / "nodes" / (SUB.split(":")[1] + ".json")
    assert node.read_bytes() == canonical_json([{"digest": B_DIGEST, "kind": "file", "name": "b.bin", "size": 1}]).encode()
    read_snapshot(folder2 / (st["digest"].split(":")[1] + ".json"))
    for d in (inline, tr):
        read_snapshot(folder / (d["digest"].split(":")[1] + ".json"))
    assert not [p for p in folder.rglob("*") if ".tmp" in p.name]


def test_default_record_area_and_optional_keys(tmp_path):
    _example(tmp_path)
    d = write_snapshot(tmp_path, "ds", grade="strict", provenance={"origin": "x", "metadata": {"k": [1, None]}})
    assert (tmp_path / "records" / "snapshots" / "v2" / (d["digest"][7:] + ".json")).is_file()
    assert d["grade"] == "strict" and d["provenance"]["origin"] == "x"
    assert d["digest"] == FLAT  # metadata never changes the content digest


def test_existing_content_keeps_first_document(tmp_path):
    _example(tmp_path)
    first = write_snapshot(tmp_path, "ds", created="2026-01-01T00:00:00Z")
    again = write_snapshot(tmp_path, "ds", created="2027-01-01T00:00:00Z")
    assert again["created"] == first["created"] == "2026-01-01T00:00:00Z"


def test_dataset_is_not_modified(tmp_path):
    _example(tmp_path)
    before = sorted(str(p) for p in (tmp_path / "ds").rglob("*"))
    mode = (tmp_path / "ds" / "a.txt").stat().st_mode
    write_snapshot(tmp_path, "ds")
    assert before == sorted(str(p) for p in (tmp_path / "ds").rglob("*"))
    assert (tmp_path / "ds" / "a.txt").stat().st_mode == mode


@pytest.mark.parametrize("layout", ["flat", "tree"])
def test_refuses_symlinks_and_special_files(tmp_path, layout):
    _example(tmp_path)
    ds = tmp_path / "ds"
    os.symlink("a.txt", ds / "link")
    with pytest.raises(SnapshotError, match="symbolic link"):
        write_snapshot(tmp_path, "ds", layout=layout)
    os.unlink(ds / "link")
    os.symlink("sub", ds / "dlink")
    with pytest.raises(SnapshotError, match="symbolic link"):
        write_snapshot(tmp_path, "ds", layout=layout)
    os.unlink(ds / "dlink")
    os.symlink("nowhere", ds / "broken")
    with pytest.raises(SnapshotError, match="symbolic link"):
        write_snapshot(tmp_path, "ds", layout=layout)
    os.unlink(ds / "broken")
    os.mkfifo(ds / "pipe")
    with pytest.raises(SnapshotError, match="not a regular"):
        write_snapshot(tmp_path, "ds", layout=layout)
    os.unlink(ds / "pipe")
    assert not (tmp_path / "records").exists()
    write_snapshot(tmp_path, "ds", layout=layout)


def test_refuses_non_utf8_name(tmp_path):
    _example(tmp_path)
    try:
        with open(os.path.join(os.fsencode(tmp_path / "ds"), b"bad\xff"), "wb") as fh:
            fh.write(b"1")
    except OSError:
        pytest.skip("file system refuses non-UTF-8 names")
    for layout in ("flat", "tree"):
        with pytest.raises(SnapshotError, match="UTF-8"):
            write_snapshot(tmp_path, "ds", layout=layout)
    assert not (tmp_path / "records").exists()


def test_refuses_missing_empty_and_bad_arguments(tmp_path):
    with pytest.raises(SnapshotError, match="does not exist"):
        write_snapshot(tmp_path, "none")
    (tmp_path / "e" / "inner").mkdir(parents=True)
    for kw in ({}, {"layout": "tree"}, {"stream": True}):
        with pytest.raises(SnapshotError, match="no regular files"):
            write_snapshot(tmp_path, "e", **kw)
    _example(tmp_path)
    with pytest.raises(SnapshotError, match="layout"):
        write_snapshot(tmp_path, "ds", layout="x")
    with pytest.raises(SnapshotError, match="stream"):
        write_snapshot(tmp_path, "ds", layout="tree", stream=True)
    with pytest.raises(SnapshotError, match="digest"):
        write_snapshot(tmp_path, "ds", previous="nope")
    with pytest.raises(SnapshotError, match="canonical"):
        write_snapshot(tmp_path, "ds", provenance={"f": 1.5})
    assert not (tmp_path / "records").exists()


def test_file_changing_during_reading_fails(tmp_path, monkeypatch):
    f = tmp_path / "f"
    f.write_bytes(b"abc")
    real = hashlib.sha256

    def fake(*a):
        h = real(*a)
        orig = h.update
        done = []

        class Wrap:
            def update(self, data):
                orig(data)
                if not done:
                    done.append(1)
                    with open(f, "ab") as fh:
                        fh.write(b"more")

            def hexdigest(self):
                return h.hexdigest()

        return Wrap()

    monkeypatch.setattr(snapshot, "hashlib", types.SimpleNamespace(sha256=fake))
    with pytest.raises(SnapshotError, match="changed"):
        file_digest(f)


def test_lineage(tmp_path):
    _example(tmp_path)
    folder = tmp_path / "records" / "snapshots" / "v2"
    first = write_snapshot(tmp_path, "ds")
    (tmp_path / "ds" / "c").write_bytes(b"c")
    with pytest.raises(SnapshotError, match="head"):
        write_snapshot(tmp_path, "ds", previous=None)
    with pytest.raises(SnapshotError, match="head"):
        write_snapshot(tmp_path, "ds", previous="sha256:" + "0" * 64)
    second = write_snapshot(tmp_path, "ds", previous=first["digest"])
    assert second["previous"] == first["digest"]
    with pytest.raises(SnapshotError, match="head"):
        write_snapshot(tmp_path, "ds", previous=first["digest"], layout="tree")
    tree = write_snapshot(tmp_path, "ds", previous=second["digest"], layout="tree")
    assert tree["previous"] == second["digest"]
    # another dataset has its own lineage
    _example(tmp_path, "other")
    assert write_snapshot(tmp_path, "other")["previous"] is None
    # two heads in the store are refused
    fake = dict(first, digest="sha256:" + "1" * 64)
    (folder / ("1" * 64 + ".json")).write_text(json.dumps(fake))
    with pytest.raises(SnapshotError, match="two heads"):
        write_snapshot(tmp_path, "ds", previous=tree["digest"])


# ---- reader ----


def _stored(tmp_path, **kw) -> Path:
    _example(tmp_path)
    d = write_snapshot(tmp_path, "ds", **kw)
    return tmp_path / "records" / "snapshots" / "v2" / (d["digest"][7:] + ".json")


def _edit(path: Path, fn) -> Path:
    doc = json.loads(path.read_text())
    fn(doc)
    out = path.with_name("edited.json")
    out.write_text(json.dumps(doc))
    return out


def test_reader_reads_v1(tmp_path):
    files = [{"path": "a.txt", "digest": A_DIGEST, "size": 6}, {"path": "sub/b.bin", "digest": B_DIGEST, "size": 1}]
    p = tmp_path / "v1.json"
    p.write_text(json.dumps({"dataset": "ds", "files": files, "digest": V1}))
    assert read_snapshot(p)["digest"] == V1
    p.write_text(json.dumps({"dataset": "ds", "files": files, "digest": FLAT}))
    with pytest.raises(SnapshotError, match="digest"):
        read_snapshot(p)


def test_reader_v1_matches_export_sealed(tmp_path):
    from stradler.workflow import export_sealed

    (tmp_path / "ds").mkdir()
    (tmp_path / "ds" / "a.txt").write_bytes(b"hello\n")
    struct = {"areas": {"rec": {"path": "records", "role": "record"}, "ds": {"path": "ds", "mutability": "sealed"}}}
    snap = export_sealed(tmp_path, "ds", struct)
    doc = read_snapshot(tmp_path / "records" / "snapshots" / (snap["digest"][7:] + ".json"))
    assert doc == snap


@pytest.mark.parametrize("version", [0, 3, "2", True, None])
def test_reader_refuses_unknown_version(tmp_path, version):
    p = _edit(_stored(tmp_path), lambda d: d.update(snapshot_version=version))
    with pytest.raises(SnapshotError, match="version"):
        read_snapshot(p)


def test_reader_refuses_unknown_key_and_bad_fields(tmp_path):
    src = _stored(tmp_path)
    for fn, msg in [
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.update(algorithm="md5"), "algorithm"),
        (lambda d: d.update(layout="x"), "layout"),
        (lambda d: d.pop("previous"), "missing key"),
        (lambda d: d.update(created="yesterday"), "timestamp"),
        (lambda d: d.update(grade=3), "grade"),
        (lambda d: d.update(provenance=[]), "provenance"),
        (lambda d: d.update(root=FLAT), "root"),
        (lambda d: d.update(file_count=0), "file_count"),
    ]:
        with pytest.raises(SnapshotError, match=msg):
            read_snapshot(_edit(src, fn))


def test_reader_recomputes_digest_count_and_size(tmp_path):
    src = _stored(tmp_path)
    with pytest.raises(SnapshotError, match="digest is not reproduced"):
        read_snapshot(_edit(src, lambda d: d["entries"][0].update(size=7)))
    with pytest.raises(SnapshotError, match="digest is not reproduced"):
        read_snapshot(_edit(src, lambda d: d.update(digest=TREE)))
    with pytest.raises(SnapshotError, match="file_count"):
        read_snapshot(_edit(src, lambda d: d.update(file_count=3)))
    with pytest.raises(SnapshotError, match="total_size"):
        read_snapshot(_edit(src, lambda d: d.update(total_size=8)))
    with pytest.raises(SnapshotError, match="sorted"):
        read_snapshot(_edit(src, lambda d: d["entries"].reverse()))
    with pytest.raises(SnapshotError, match="members"):
        read_snapshot(_edit(src, lambda d: d["entries"][0].update(mode=1)))


def test_reader_checks_stream_and_nodes(tmp_path):
    src = _stored(tmp_path, stream=True)
    stream = src.with_name(FLAT[7:] + ".entries.jsonl")
    assert read_snapshot(src)["file_count"] == 2
    good = stream.read_bytes()
    stream.write_bytes(good.replace(b'"size":1', b'"size":2'))
    with pytest.raises(SnapshotError, match="digest is not reproduced"):
        read_snapshot(src)
    stream.write_bytes(good.rstrip(b"\n"))
    with pytest.raises(SnapshotError, match="line feed"):
        read_snapshot(src)
    stream.write_bytes(good.replace(b'{"digest"', b'{ "digest"', 1))
    with pytest.raises(SnapshotError, match="canonical"):
        read_snapshot(src)
    stream.write_bytes(good)
    stream.unlink()
    with pytest.raises(SnapshotError, match="entry stream"):
        read_snapshot(src)

    tsrc = _stored(tmp_path / "t", layout="tree")
    node = tsrc.parent / "nodes" / (SUB[7:] + ".json")
    assert read_snapshot(tsrc)["root"] == ROOT
    good = node.read_bytes()
    node.write_bytes(good.replace(b'"size":1', b'"size":2'))
    with pytest.raises(SnapshotError, match="node file"):
        read_snapshot(tsrc)
    node.unlink()
    with pytest.raises(SnapshotError, match="cannot read node"):
        read_snapshot(tsrc)


def test_reader_refuses_unreadable_document(tmp_path):
    with pytest.raises(SnapshotError):
        read_snapshot(tmp_path / "missing.json")
    p = tmp_path / "x.json"
    p.write_text("[]")
    with pytest.raises(SnapshotError, match="object"):
        read_snapshot(p)


# ---- conformance cases (SNAPSHOTS.md section 9) ----

CASES = sorted(p.name for p in CONF.iterdir()) if CONF.is_dir() else []


def test_required_cases_exist():
    assert {"example", "empty-dir", "nested-depth", "non-ascii", "zero-byte", "base64-binary"} <= set(CASES)


@pytest.mark.parametrize("case", CASES)
def test_conformance_case(case, tmp_path):
    inp = json.loads((CONF / case / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((CONF / case / "expected.json").read_text(encoding="utf-8"))
    assert [f["path"] for f in inp["files"]] == sorted((f["path"] for f in inp["files"]), key=lambda s: s.encode("utf-8"))
    assert exp == reference(inp), "stored values differ from the independent reference"
    entries = list(snapshot.iter_entries(materialize(inp, tmp_path / "v1" / "ds")))
    assert digest_of(entries) == exp["v1"]["digest"]
    assert (len(entries), sum(e["size"] for e in entries)) == (exp["v1"]["file_count"], exp["v1"]["total_size"])
    for key, kw in (("flat", {}), ("flat", {"stream": True}), ("tree", {"layout": "tree"})):
        root = tmp_path / f"p-{key}-{len(kw)}"
        materialize(inp, root / "ds")
        d = write_snapshot(root, "ds", **kw)
        want = exp[key]
        assert d["digest"] == want["digest"]
        assert (d["file_count"], d["total_size"]) == (want["file_count"], want["total_size"])
        if key == "tree":
            assert d["root"] == want["root"]
        read_snapshot(root / "records" / "snapshots" / "v2" / (d["digest"][7:] + ".json"))
