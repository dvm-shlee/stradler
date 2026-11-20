import json
import tomllib
from pathlib import Path

import pytest
from test_structure_roles_grade import STRUCT_11

from stradler import tomlw
from stradler.cli import main
from stradler.describe import DESCRIBE_VERSION, agent_view, describe_project
from stradler.init import init_project
from stradler.project import load_project

TEMPLATE = Path(__file__).parent / "data" / "template.yaml"
SECRET = "SECRET-LOCAL-VALUE-123"
DOC_KEYS = ["describe_version", "project", "areas", "user_roles", "stores", "addons", "pins", "replicas"]
AREA_KEYS = ["name", "path", "role", "base_role", "mutability", "grade", "store", "manifest", "agent",
             "deletable_after"]


@pytest.fixture
def proj(tmp_path):
    root = init_project(TEMPLATE, tmp_path / "p")
    (root / ".stradler" / "local.toml").write_text(
        f'[stores.shared-sif]\npath = "/{SECRET}/dir"\nendpoint = "{SECRET}.example"\n'
        f'[addons.nas]\nsettings = {{ token = "{SECRET}" }}\n')
    return root


def areas(doc):
    return {a["name"]: a for a in doc["areas"]}


def test_1_0_0_project(proj):
    doc = describe_project(load_project(proj))
    assert list(doc) == DOC_KEYS and doc["describe_version"] == DESCRIBE_VERSION == 2
    assert doc["project"]["structure_schema"] == "structure@1.0.0"
    assert all(list(a) == AREA_KEYS for a in doc["areas"])
    a = areas(doc)
    assert a["rawdata"]["grade"] == "private"  # default made explicit
    assert a["rawdata"]["agent"] == {"search": True, "read": True, "write": False}
    assert a["code"]["agent"] == {"search": False, "read": False, "write": False}
    assert a["images"]["store"] == "shared-sif" and a["images"]["path"] is None
    assert a["checkpoints"]["deletable_after"] == ["derivatives"]
    assert a["derivatives"]["manifest"] == "dataset_description.json"
    assert doc["user_roles"] == [] and doc["stores"] == ["shared-sif"]
    assert {"name": "nas", "kind": "store"} in doc["addons"]
    assert doc["pins"] == [{"name": "stradler", "ref": "0.1.0a1"}]


def test_1_1_0_project(proj):
    (proj / ".stradler" / "structure.toml").write_text(STRUCT_11)
    doc = describe_project(load_project(proj))
    assert doc["project"]["structure_schema"] == "structure@1.1.0"
    a = areas(doc)
    assert (a["vault"]["role"], a["vault"]["base_role"], a["vault"]["grade"]) == ("archive", "input", "confidential")
    assert a["work"]["role"] == "stage" and a["work"]["base_role"] == "output"
    assert [r["name"] for r in doc["user_roles"]] == ["archive", "document", "stage"]
    r = {r["name"]: r for r in doc["user_roles"]}
    assert r["archive"]["min_grade"] == "work"
    assert r["stage"]["mutability"] == ["immutable", "sealed", "mutable"] and r["stage"]["agent"] is None
    assert r["document"]["agent"] == {"search": True, "read": True, "write": True}


def test_local_toml_never_appears(proj, capsys):
    for argv in (["describe", str(proj)], ["describe", "--json", str(proj)], ["describe", "--agent", str(proj)],
                 ["describe", "--agent", "--json", str(proj)]):
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert SECRET not in out and "endpoint" not in out and "token" not in out


def test_stable_ordering(proj, capsys):
    outs = []
    for _ in range(2):
        main(["describe", "--json", str(proj)])
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1]
    doc = json.loads(outs[0])
    assert list(doc) == DOC_KEYS
    names = [a["name"] for a in doc["areas"]]
    assert names == sorted(names)
    d = tomllib.loads((proj / ".stradler" / "structure.toml").read_text())
    d["areas"] = dict(reversed(list(d["areas"].items())))
    (proj / ".stradler" / "structure.toml").write_text(tomlw.dumps(d))
    main(["describe", "--json", str(proj)])
    assert capsys.readouterr().out == outs[0]


def test_agent_view_and_text(proj, capsys):
    view = agent_view(describe_project(load_project(proj)))
    assert all(list(a) == ["area", "role", "agent", "grade"] for a in view["areas"])
    main(["describe", "--agent", "--json", str(proj)])
    assert json.loads(capsys.readouterr().out) == view
    main(["describe", str(proj)])
    text = capsys.readouterr().out
    assert "rawdata" in text and "grade private" in text
    main(["describe", "--agent", str(proj)])
    assert "mutab" not in capsys.readouterr().out


def test_no_project(tmp_path):
    assert main(["describe", str(tmp_path)]) == 1
