import json
from pathlib import Path

import pytest

from stradler import schema as sch

ROOT = Path(__file__).resolve().parent.parent


def test_three_core_schemas_present_and_json():
    files = sorted((ROOT / "schemas").glob("*@1.0.0.json"))
    assert [f.name for f in files] == ["config@1.0.0.json", "stradler@1.0.0.json", "structure@1.0.0.json"]
    for f in files:
        json.loads(f.read_text())


def test_stradler_json_example():
    s = sch.load_bundled("stradler")
    ok = {"marker": "stradler-project", "spec_version": "1.0.0", "project_id": "p", "config_dir": ".stradler"}
    assert sch.validate(ok, s) == []
    assert sch.validate({**ok, "extra": 1}, s)
    assert sch.validate({**ok, "marker": "x"}, s)
    assert sch.validate({**ok, "spec_version": "1.0"}, s)
    assert sch.validate({k: v for k, v in ok.items() if k != "project_id"}, s)


def test_structure_path_rules():
    s = sch.load_bundled("structure")

    def check(area):
        return sch.validate({"schema": "structure@1.0.0", "areas": {"a": area}}, s)

    assert check({"role": "code", "path": "code", "mutability": "mutable"}) == []
    assert check({"role": "image", "store": "x", "mutability": "immutable"}) == []
    assert check({"role": "code", "path": "code"})  # mutability is required
    assert check({"role": "code", "path": "code", "mutability": "other"})
    for bad in ("", "/abs", ".", "../up", "a/../b", ".stradler", ".stradler/x"):
        assert check({"role": "code", "path": bad, "mutability": "mutable"}), bad
    assert check({"role": "code"})
    assert check({"role": "code", "path": "p", "store": "x"})
    assert check({"role": "nope", "path": "p"})
    assert check({"role": "code", "path": "p", "manifest": "x.yaml"})
    assert check({"role": "code", "path": "p", "agent": {"search": "yes"}})
    assert check({"role": "code", "path": "p", "deletable_after": ["a", "a"]})
    assert sch.validate({"schema": "structure@1.0.0", "areas": {}}, s)


def test_config_schema():
    s = sch.load_bundled("config")
    good = {"schema": "config@1.0.0", "addons": {"a": {"kind": "store", "version": "0.1.0", "command": "x"}},
            "stores": {"s": {"addon": "a"}}, "pins": {"stradler": {"ref": "0.1.0a1"}}}
    assert sch.validate(good, s) == []
    assert sch.validate({"schema": "config@1.0.0", "addons": {"a": {"kind": "bogus", "version": "0.1.0", "command": "x"}}}, s)
    assert sch.validate({"schema": "config@1.0.0", "other": 1}, s)


def test_unsupported_keyword_raises():
    with pytest.raises(sch.SchemaError):
        sch.validate(1, {"oneOf": []})


def test_coerce():
    s = {"type": "object", "properties": {"b": {"type": "boolean"}, "n": {"type": "integer"}, "s": {"type": "string"}}}
    assert sch.coerce({"b": "true", "n": "12", "s": "true"}, s) == {"b": True, "n": 12, "s": "true"}
    assert sch.coerce({"b": "yes", "n": "1.0"}, s) == {"b": "yes", "n": "1.0"}
