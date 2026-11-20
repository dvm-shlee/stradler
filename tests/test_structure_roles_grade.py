import copy
import json
import os
import tomllib
from pathlib import Path

import pytest

from stradler import tomlw
from stradler.init import init_project
from stradler.project import load_project
from stradler.validate import validate_project
from stradler.workflow import (
    ExportError,
    PlanError,
    build_plan,
    export_sealed,
    parse_recipe,
    run_workflow,
)
from stradler.workflow.areas import AreaIndex

TEMPLATE = Path(__file__).parent / "data" / "template.yaml"
ROOT = Path(__file__).parent.parent

STRUCT_11 = """
schema = "structure@1.1.0"

[roles.archive]
base = "input"
description = "Frozen deposits."
mutability = ["immutable", "sealed"]
min_grade = "work"

[roles.document]
base = "input"
description = "Editable documents."
mutability = ["mutable"]
agent = { search = true, read = true, write = true }

[roles.stage]
base = "output"
description = "Intermediate files."

[areas.texts]
role = "document"
path = "texts"
mutability = "mutable"
grade = "work"

[areas.vault]
role = "archive"
path = "vault"
mutability = "sealed"
grade = "confidential"

[areas.work]
role = "stage"
path = "work"
mutability = "mutable"
grade = "work"

[areas.open]
role = "output"
path = "open"
mutability = "mutable"
grade = "public"

[areas.records]
role = "record"
path = "records"
mutability = "mutable"
"""


@pytest.fixture
def proj(tmp_path):
    return init_project(TEMPLATE, tmp_path / "p")


def put(root, text):
    (root / ".stradler" / "structure.toml").write_text(text)


def problems(root):
    return validate_project(load_project(root))


def with_struct(proj, fn):
    d = tomllib.loads(STRUCT_11)
    fn(d)
    put(proj, tomlw.dumps(d))
    return problems(proj)


def test_schema_file_present_and_1_0_0_unchanged(proj):
    s = json.loads((ROOT / "schemas" / "structure@1.1.0.json").read_text())
    assert s["$id"] == "structure@1.1.0"
    assert "roles" in s["properties"]
    old = json.loads((ROOT / "schemas" / "structure@1.0.0.json").read_text())
    assert "roles" not in old["properties"] and "grade" not in old["properties"]["areas"]["additionalProperties"]["properties"]
    assert problems(proj) == []  # a 1.0.0 project stays valid


def test_valid_1_1_0_project(proj):
    put(proj, STRUCT_11)
    assert problems(proj) == []


def test_1_0_0_rejects_new_keys(proj):
    d = tomllib.loads((proj / ".stradler" / "structure.toml").read_text())
    d["areas"]["code"]["grade"] = "work"
    put(proj, tomlw.dumps(d))
    assert any("grade" in p for p in problems(proj))


def test_base_missing_and_bad(proj):
    def drop(d):
        del d["roles"]["stage"]["base"]
    assert any("missing required key 'base'" in p for p in with_struct(proj, drop))

    def bad(d):
        d["roles"]["stage"]["base"] = "other"
    assert problems_has(with_struct(proj, bad), "roles.stage.base")

    def chain(d):
        d["roles"]["stage"]["base"] = "archive"
    assert problems_has(with_struct(proj, chain), "one level only") or problems_has(with_struct(proj, chain), "not one of")


def problems_has(ps, text):
    return any(text in p for p in ps)


def test_core_name_clash(proj):
    def clash(d):
        d["roles"]["input"] = {"base": "input"}
    assert problems_has(with_struct(proj, clash), "roles.input: name equals a core role")


def test_loosening_role(proj):
    def loosen(d):
        d["roles"]["archive"]["agent"] = {"write": True}
    assert problems_has(with_struct(proj, loosen), "never loosen")


def test_empty_mutability_and_disallowed_mutability(proj):
    def empty(d):
        d["roles"]["stage"]["mutability"] = []
    assert with_struct(proj, empty)

    def bad(d):
        d["areas"]["vault"]["mutability"] = "mutable"
    assert problems_has(with_struct(proj, bad), "not allowed by role 'archive'")


def test_area_agent_beyond_role_bound(proj):
    def over(d):
        d["roles"]["document"]["agent"] = {"read": True}
        d["areas"]["texts"]["agent"] = {"write": True}
    assert problems_has(with_struct(proj, over), "areas.texts.agent.write")


def test_grade_below_min_grade(proj):
    def low(d):
        d["areas"]["vault"]["grade"] = "public"
    assert problems_has(with_struct(proj, low), "less strict than min_grade")

    def absent(d):
        del d["areas"]["vault"]["grade"]  # absent means private: fine
    assert with_struct(proj, absent) == []


def test_unknown_role_and_bad_grade(proj):
    def unknown(d):
        d["areas"]["work"]["role"] = "nothing"
    assert problems_has(with_struct(proj, unknown), "neither a core role nor defined")

    def bad(d):
        d["areas"]["work"]["grade"] = "secret"
    assert problems_has(with_struct(proj, bad), "grade")


def test_user_role_area_is_its_base():
    idx = AreaIndex(tomllib.loads(STRUCT_11))
    assert idx.areas["texts"]["role"] == "input" and idx.areas["texts"]["role_name"] == "document"
    assert idx.has_role("texts/a.txt", ("input",))
    assert idx.has_role("work/x", ("output", "record"))
    assert idx.mutability_of("vault/f") == "sealed"
    assert idx.grade_of("vault/f") == "confidential"
    assert idx.grade_of("records/r") == "private"
    assert idx.grade_of("elsewhere/x") is None


HEAD = """schema: workflow@1.0.0
name: t.flow
family: t
version: 1.0.0
purpose: Test.
inputs:
  dataset: texts
steps:
  - name: one
    purpose: First.
    level: file
    container: none
    command: [cp, "${source}", "${output}"]
    in:
      - as: source
        from: inputs
    out:
      - name: o
        path: %s/{stem}.one
"""


@pytest.fixture
def root(tmp_path):
    for d in ("texts", "work", "open", "vault", "records"):
        (tmp_path / d).mkdir()
    (tmp_path / "texts" / "a.txt").write_text("a")
    return tmp_path


def test_user_role_areas_behave_like_base_in_execute_and_export(root):
    struct = tomllib.loads(STRUCT_11)
    recipe = parse_recipe(HEAD % "work", areas=struct)  # work is a stage (output) area, texts an input-based one
    res = run_workflow(recipe, root, struct)
    assert res.status == "ok"
    assert (root / "work" / "a.one").read_text() == "a"
    (root / "vault" / "x.txt").write_text("x")
    assert export_sealed(root, "vault", struct)["files"][0]["path"] == "x.txt"
    with pytest.raises(ExportError):
        export_sealed(root, "texts", struct)


def test_plan_refuses_loosening_step(root):
    struct = tomllib.loads(STRUCT_11)
    recipe = parse_recipe(HEAD % "open", areas=struct)  # work-grade input -> public output
    with pytest.raises(PlanError, match="looser"):
        build_plan(recipe, root, struct)


def test_plan_accepts_equal_or_stricter(root):
    struct = tomllib.loads(STRUCT_11)
    assert build_plan(parse_recipe(HEAD % "work", areas=struct), root, struct).segments
    s2 = copy.deepcopy(struct)
    s2["areas"]["work"]["grade"] = "private"
    assert build_plan(parse_recipe(HEAD % "work", areas=s2), root, s2).segments
    del s2["areas"]["work"]["grade"]  # absent = private
    assert build_plan(parse_recipe(HEAD % "work", areas=s2), root, s2).segments


def test_projects_without_grades_unaffected(root):
    struct = tomllib.loads(STRUCT_11)
    for a in struct["areas"].values():
        a.pop("grade", None)
    assert build_plan(parse_recipe(HEAD % "open", areas=struct), root, struct).segments
    assert os.path.isdir(root / "open")
