"""Replica declarations (config@1.1.0) and checks of declared copies (docs/SNAPSHOTS.md section 7.6)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from stradler import schema as sch
from stradler.cli import main
from stradler.describe import describe_project
from stradler.project import load_project
from stradler.replicas import ReplicaError, replica_status, verify_replicas
from stradler.seal import seal_dataset
from stradler.validate import validate_project

FAKES = Path(__file__).parent / "fakes"
STRUCT = """schema = "structure@1.0.0"
[areas.rec]
path = "records"
role = "record"
mutability = "mutable"
[areas.out]
path = "out"
role = "output"
mutability = "sealed"
[areas.far]
role = "output"
mutability = "sealed"
store = "cloud"
"""


def _unlock(root: Path) -> None:
    for cur, dirs, files in os.walk(root):
        os.chmod(cur, 0o755)
        for f in files:
            p = os.path.join(cur, f)
            if not os.path.islink(p):
                os.chmod(p, 0o644)


def config(replicas: str = "", extra_stores: str = "", schema: str = "config@1.1.0") -> str:
    return f"""schema = "{schema}"
[addons.nas]
kind = "store"
version = "0.1.0"
command = "/bin/true"
[addons.cloud]
kind = "remote"
version = "0.1.0"
command = "{{cmd}}"
[stores.disk]
addon = "nas"
[stores.disk2]
addon = "nas"
[stores.cloud]
addon = "cloud"
{extra_stores}{replicas}"""


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".stradler").mkdir(parents=True)
    (root / "stradler.json").write_text(json.dumps({"marker": "stradler-project", "project_id": "t", "spec_version": "1.0.0",
                                                    "config_dir": ".stradler"}))
    (root / ".stradler" / "structure.toml").write_text(STRUCT)
    ds = root / "out" / "ds"
    (ds / "sub").mkdir(parents=True)
    (ds / "a.txt").write_text("alpha")
    (ds / "sub" / "b.txt").write_text("beta")
    wrapper = tmp_path / "fake_store.sh"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKES}/fake_store.py "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.chdir(root)
    write_config(root, wrapper, '[replicas.out]\nstores = ["disk", "disk2"]\nmin_copies = 2\n')
    yield root
    _unlock(tmp_path)


def write_config(root, wrapper, replicas, schema="config@1.1.0"):
    (root / ".stradler" / "config.toml").write_text(config(replicas, schema=schema).replace("{cmd}", str(wrapper)))


def set_config(root, replicas, schema="config@1.1.0"):
    wrapper = root.parent / "fake_store.sh"
    write_config(root, wrapper, replicas, schema)


def seal(root):
    prj = load_project(root)
    return seal_dataset(str(root), prj.structure, "out/ds")


def make_copy(root, name):
    dest = root.parent / name
    shutil.copytree(root / "out" / "ds", dest / "out" / "ds")
    _unlock(dest)
    return dest


def set_local(root, **stores):
    text = "".join(f'[stores.{n}]\npath = "{p}"\n' for n, p in stores.items())
    (root / ".stradler" / "local.toml").write_text(text)


def problems(root):
    return validate_project(load_project(root))


# ---- config@1.1.0 ----


def test_schema_file_and_valid_declaration(proj):
    assert sch.load_bundled("config", "1.1.0")["$id"] == "config@1.1.0"
    assert problems(proj) == []


def test_config_1_0_0_unchanged_and_refuses_replicas(proj):
    assert "replicas" not in sch.load_bundled("config", "1.0.0")["properties"]
    set_config(proj, '[replicas.out]\nstores = ["disk"]\n', schema="config@1.0.0")
    assert any("unknown key 'replicas'" in p for p in problems(proj))
    set_config(proj, "", schema="config@1.0.0")
    assert problems(proj) == []


@pytest.mark.parametrize("decl, text", [
    ('[replicas.out]\nstores = ["nope"]\n', "unknown store 'nope'"),
    ('[replicas.out]\nstores = ["disk", "disk"]\n', "must be unique"),
    ('[replicas.out]\nstores = []\n', "at least 1"),
    ('[replicas.far]\nstores = ["cloud"]\n', "area's own store"),
    ('[replicas.ghost]\nstores = ["disk"]\n', "no such area"),
    ('[replicas.out]\nstores = ["disk"]\nmin_copies = 2\n', "more than the 1 declared"),
    ('[replicas.out]\nstores = ["disk"]\nmin_copies = 0\n', "less than 1"),
    ('[replicas.out]\nstores = ["disk"]\nmin_copies = "1"\n', "expected integer"),
    ('[replicas.out]\nstores = ["disk"]\nextra = 1\n', "unknown key 'extra'"),
    ('[replicas.out]\nmin_copies = 1\n', "missing required key 'stores'"),
])
def test_validation_refusals(proj, decl, text):
    set_config(proj, decl)
    assert any(text in p for p in problems(proj)), problems(proj)


def test_own_store_via_true_sources(proj):
    set_config(proj, '[true_sources.out]\nstore = "disk"\n[replicas.out]\nstores = ["disk", "disk2"]\n')
    assert any("area's own store" in p for p in problems(proj))


# ---- verify --replicas and describe ----


def test_local_stores_records_and_summary(proj, capsys):
    seal(proj)
    c1, c2 = make_copy(proj, "c1"), make_copy(proj, "c2")
    set_local(proj, disk=c1, disk2=c2)
    out = verify_replicas(load_project(proj), "out")
    assert [c["result"] for c in out["checks"]] == ["match", "match"]
    assert sorted(p.name.split("-")[1] for p in (proj / "records" / "checks").glob("*.json")
                  if "disk" in p.name) == ["disk", "disk2"]
    rows = replica_status(load_project(proj))
    assert [(r["store"], r["result"], r["min_copies_met"]) for r in rows] == [("disk", "match", True),
                                                                              ("disk2", "match", True)]
    assert main(["verify", "--replicas", "out", "--json"]) == 0
    capsys.readouterr()
    assert main(["describe", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["replicas"] == describe_project(load_project(proj))["replicas"]
    doc = describe_project(load_project(proj))
    assert doc["describe_version"] == 2
    assert all(r["declared"] and r["last_check"] for r in doc["replicas"])


def test_differs_and_min_copies_not_met(proj):
    seal(proj)
    c1, c2 = make_copy(proj, "c1"), make_copy(proj, "c2")
    (c2 / "out" / "ds" / "a.txt").write_text("tampered")
    set_local(proj, disk=c1, disk2=c2)
    out = verify_replicas(load_project(proj), "out")
    assert [c["result"] for c in out["checks"]] == ["match", "differs"]
    assert main(["verify", "--replicas", "out"]) == 1
    rows = {r["store"]: r for r in replica_status(load_project(proj))}
    assert rows["disk"]["result"] == "match" and rows["disk2"]["result"] == "differs"
    assert rows["disk"]["min_copies_met"] is False


def test_offline_store_is_unreachable(proj):
    seal(proj)
    set_local(proj, disk=make_copy(proj, "c1"), disk2=proj.parent / "gone")
    out = verify_replicas(load_project(proj), "out")
    assert [c["result"] for c in out["checks"]] == ["match", "unreachable"]
    recs = [json.loads(p.read_text()) for p in (proj / "records" / "checks").glob("*.json")]
    assert any(r["result"] == "unreachable" and "counts" not in r for r in recs)
    assert main(["verify", "--replicas", "out"]) == 2
    rows = {r["store"]: r for r in replica_status(load_project(proj))}
    assert rows["disk2"]["result"] == "unreachable" and rows["disk2"]["min_copies_met"] is False


def test_only_latest_snapshot_is_checked_and_status_resets(proj):
    seal(proj)
    c1, c2 = make_copy(proj, "c1"), make_copy(proj, "c2")
    set_local(proj, disk=c1, disk2=c2)
    verify_replicas(load_project(proj), "out")
    _unlock(proj)
    (proj / "out" / "ds" / "d.txt").write_text("delta")
    seal(proj)
    rows = replica_status(load_project(proj))
    assert all(r["result"] is None and r["last_check"] is None for r in rows)
    out = verify_replicas(load_project(proj), "out")
    assert len(out["checks"]) == 2 and all(c["result"] == "differs" for c in out["checks"])


def test_no_declaration_and_usage_errors(proj):
    assert main(["verify", "--replicas", "far"]) == 3
    assert main(["verify"]) == 3
    assert main(["verify", "--replicas", "out", "out/ds"]) == 3
    with pytest.raises(ReplicaError):
        verify_replicas(load_project(proj), "rec")


def test_describe_without_replicas(proj):
    set_config(proj, "")
    assert describe_project(load_project(proj))["replicas"] == []


# ---- add-on digest ----


def remote_config(proj, mode, root_dir):
    set_config(proj, '[replicas.out]\nstores = ["cloud"]\n')
    (proj / ".stradler" / "local.toml").write_text(
        f'[addons.cloud.settings]\nmode = "{mode}"\nroot = "{root_dir}"\n')


def test_fake_addon_digest_match_and_changed(proj):
    seal(proj)
    c = make_copy(proj, "remote")
    remote_config(proj, "ok", c)
    assert problems(proj) == []
    out = verify_replicas(load_project(proj), "out")
    assert out["checks"][0]["result"] == "match" and out["checks"][0]["counts"]["match"] == 2
    (c / "out" / "ds" / "sub" / "b.txt").write_text("BETA")
    (c / "out" / "ds" / "a.txt").unlink()
    out = verify_replicas(load_project(proj), "out")
    chk = out["checks"][0]
    assert chk["result"] == "differs" and chk["counts"]["changed"] == 1 and chk["counts"]["missing"] == 1
    assert json.loads(Path(chk["record"]).read_text())["store"] == "cloud"


def test_addon_without_digest_reports_not_supported(proj, capsys):
    seal(proj)
    before = sorted((proj / "records" / "checks").glob("*"))
    remote_config(proj, "unsupported", proj)
    chk = verify_replicas(load_project(proj), "out")["checks"][0]
    assert chk["result"] == "not_supported" and "verify not supported" in chk["message"] and "record" not in chk
    assert sorted((proj / "records" / "checks").glob("*")) == before
    assert main(["verify", "--replicas", "out"]) == 3
    assert "verify not supported" in capsys.readouterr().out
    assert replica_status(load_project(proj))[0]["result"] is None


def test_addon_offline_is_unreachable(proj):
    seal(proj)
    remote_config(proj, "down", proj)
    chk = verify_replicas(load_project(proj), "out")["checks"][0]
    assert chk["result"] == "unreachable"
    rec = json.loads(Path(chk["record"]).read_text())
    assert rec["result"] == "unreachable" and rec["store"] == "cloud" and "counts" not in rec
