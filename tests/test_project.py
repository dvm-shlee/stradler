import json
import tomllib
from pathlib import Path

import pytest

from stradler import tomlw
from stradler.cli import main
from stradler.init import InitError, init_project, load_template
from stradler.project import find_root, load_project
from stradler.validate import validate_project

TEMPLATE = Path(__file__).parent / "data" / "template.yaml"


@pytest.fixture
def proj(tmp_path):
    return init_project(TEMPLATE, tmp_path / "myproj")


def problems(root):
    return validate_project(load_project(root))


def edit_structure(root, fn):
    p = root / ".stradler" / "structure.toml"
    d = tomllib.loads(p.read_text())
    fn(d)
    p.write_text(tomlw.dumps(d))


def test_init_then_validate(proj):
    assert problems(proj) == []
    marker = json.loads((proj / "stradler.json").read_text())
    assert marker == {"marker": "stradler-project", "spec_version": "1.0.0", "project_id": "myproj", "config_dir": ".stradler"}
    cd = proj / ".stradler"
    for f in ("config.toml", "structure.toml", "template.json", "local.toml", ".gitignore"):
        assert (cd / f).is_file()
    assert sorted(p.name for p in (cd / "schema").iterdir()) == [
        "config@1.0.0.json", "stradler@1.0.0.json", "structure@1.0.0.json"]
    assert (proj / "rawdata").is_dir() and (proj / "agent").is_dir() and not (proj / "images").exists()
    areas = tomllib.loads((cd / "structure.toml").read_text())["areas"]
    assert areas["agent"]["agent"] == {"search": True, "read": True, "write": True}
    assert areas["checkpoints"]["deletable_after"] == ["derivatives"]
    tpl = json.loads((cd / "template.json").read_text())
    assert tpl["name"] == "sample" and tpl["digest"].startswith("sha256:")


def test_discovery_walks_up(proj):
    deep = proj / "code" / "a" / "b"
    deep.mkdir(parents=True)
    assert find_root(deep) == proj.resolve()
    assert load_project(deep).root == proj.resolve()


def test_cli_init_validate_json(tmp_path, capsys):
    target = tmp_path / "p"
    assert main(["init", "--template", str(TEMPLATE), "--project-id", "xid", "--json", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["validate", "--json", str(target / "code")]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "root": str(target.resolve()), "problems": [], "warnings": []}
    assert json.loads((target / "stradler.json").read_text())["project_id"] == "xid"



def test_init_refuses_existing(proj):
    with pytest.raises(InitError, match="already"):
        init_project(TEMPLATE, proj)


def test_write_exceeds_mutability(proj):
    def f(d):
        d["areas"]["rawdata"]["agent"]["write"] = True
        d["areas"]["derivatives"]["agent"] = {"write": True}
    edit_structure(proj, f)
    msgs = problems(proj)
    assert any("areas.rawdata" in m and "immutable" in m for m in msgs)
    assert any("areas.derivatives" in m and "sealed" in m for m in msgs)
    assert not any("areas.agent" in m for m in msgs)


def test_write_on_mutable_ok(proj):
    edit_structure(proj, lambda d: d["areas"]["code"].update(agent={"write": True}))
    assert problems(proj) == []


def test_cross_reference_errors(proj):
    def f(d):
        d["areas"]["code"]["handler"] = "nope"
        d["areas"]["records"]["handler"] = "nas"  # exists but not a data_handler
        d["areas"]["images"]["store"] = "unknown-store"
        d["areas"]["checkpoints"]["deletable_after"] = ["ghost"]
        d["areas"]["reports"] = {"role": "output", "path": "code", "mutability": "mutable"}
    edit_structure(proj, f)
    msgs = "\n".join(problems(proj))
    assert "areas.code.handler" in msgs and "areas.records.handler" in msgs
    assert "unknown store 'unknown-store'" in msgs
    assert "unknown area 'ghost'" in msgs
    assert "areas.reports.path: same as areas.code" in msgs


def test_schema_shape_errors(proj):
    edit_structure(proj, lambda d: d["areas"]["code"].update(role="bogus", extra=1))
    msgs = "\n".join(problems(proj))
    assert "structure.toml" in msgs and "bogus" in msgs and "unknown key 'extra'" in msgs


def test_missing_and_broken_files(proj):
    (proj / ".stradler" / "structure.toml").unlink()
    (proj / ".stradler" / "config.toml").write_text("not = = toml")
    msgs = problems(proj)
    assert any("structure.toml: file is missing" in m for m in msgs)
    assert any("config.toml: cannot parse" in m for m in msgs)


def test_local_toml_names_must_exist(proj):
    (proj / ".stradler" / "local.toml").write_text('[stores.ghost]\npath = "/x"\n')
    assert any("local.toml: stores.ghost" in m for m in problems(proj))


def test_true_sources_rules(proj):
    p = proj / ".stradler" / "config.toml"
    d = tomllib.loads(p.read_text())
    d["true_sources"] = {"rawdata": {"store": "shared-sif"}, "ghost": {"store": "shared-sif"}}
    p.write_text(tomlw.dumps(d))
    edit_structure(proj, lambda s: s["areas"]["rawdata"].update(source="other"))
    msgs = "\n".join(problems(proj))
    assert "true_sources.ghost: no such area" in msgs
    assert "unknown store 'other'" in msgs


def test_bad_templates(tmp_path):
    def t(name, body):
        p = tmp_path / name
        p.write_text(body)
        return p

    with pytest.raises(InitError, match="not found"):
        load_template(tmp_path / "none.yaml")
    with pytest.raises(InitError, match="missing 'areas'"):
        load_template(t("a.yaml", "name: x\nversion: 0.1.0\n"))
    with pytest.raises(InitError, match="version"):
        load_template(t("b.yaml", "name: x\nversion: 1\nareas:\n  a:\n    role: code\n    path: c\n"))
    with pytest.raises(InitError):
        load_template(t("c.yaml", "name: &a x\nversion: 0.1.0\nareas: {}\n"))
    with pytest.raises(InitError, match="extension"):
        load_template(t("d.txt", "x"))
    # an invalid project leaves nothing behind
    bad = t("e.yaml", "name: x\nversion: 0.1.0\nareas:\n  a:\n    role: input\n    path: ../out\n    mutability: mutable\n")
    target = tmp_path / "t"
    with pytest.raises(InitError, match="invalid project"):
        init_project(bad, target)
    assert not target.exists()
    bad2 = t("f.yaml", "name: x\nversion: 0.1.0\nareas:\n  a:\n    role: input\n    path: raw\n    mutability: immutable\n    agent:\n      write: true\n")
    with pytest.raises(InitError, match="write never exceeds"):
        init_project(bad2, target)
    assert not target.exists()


def test_json_and_toml_templates(tmp_path):
    areas = {"a": {"role": "code", "path": "code", "mutability": "mutable"}}
    j = tmp_path / "t.json"
    j.write_text(json.dumps({"name": "j", "version": "1.2.3", "areas": areas}))
    tm = tmp_path / "t.toml"
    tm.write_text(tomlw.dumps({"name": "t", "version": "1.2.3", "areas": areas}))
    for i, f in enumerate((j, tm)):
        root = init_project(f, tmp_path / f"p{i}")
        assert problems(root) == []


def test_missing_mutability_fails(proj):
    edit_structure(proj, lambda d: d["areas"]["code"].pop("mutability"))
    msgs = "\n".join(problems(proj))
    assert "structure.toml" in msgs and "mutability" in msgs
