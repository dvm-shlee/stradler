"""Conformance cases stored as files in tests/conformance (WORKFLOWS.md section 11)."""

import datetime
import json
import os
import shutil
import tomllib
from pathlib import Path

import pytest

from stradler import yamlsubset
from stradler.workflow import (
    RecipeError,
    build_plan,
    canonical_json,
    input_digest,
    parse_recipe,
    sha256_digest,
)

CONF = Path(__file__).parent / "conformance"


def cases(kind):
    base = CONF / kind
    return sorted(p for p in base.iterdir() if p.is_dir())


def ids(paths):
    return [p.name for p in paths]


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted((CONF / "yaml").glob("*.yaml")), ids=lambda p: p.stem)
def test_yaml_cases(path):
    expect = read_json(path.with_suffix(".expect.json"))
    text = path.read_text(encoding="utf-8")
    if expect["accept"]:
        assert yamlsubset.loads(text) == expect["value"]
    else:
        with pytest.raises(yamlsubset.YamlSubsetError):
            yamlsubset.loads(text)
        with pytest.raises(RecipeError) as exc:
            parse_recipe(text)
        assert [p.code for p in exc.value.problems] == [expect["code"]]


@pytest.mark.parametrize("case", read_json(CONF / "canonical" / "cases.json"))
def test_canonical_cases(case):
    text = canonical_json(case["value"])
    assert text == case["canonical"]
    assert sha256_digest(text.encode("utf-8")) == case["digest"]


@pytest.mark.parametrize("path", cases("recipe"), ids=ids(cases("recipe")))
def test_recipe_cases(path):
    expect = read_json(path / "expect.json")
    structure = tomllib.loads((path / "structure.toml").read_text(encoding="utf-8"))
    text = (path / "recipe.yaml").read_text(encoding="utf-8")
    if expect["accept"]:
        parse_recipe(text, areas=structure)
        return
    with pytest.raises(RecipeError) as exc:
        parse_recipe(text, areas=structure)
    got = {(p.code, p.path) for p in exc.value.problems}
    assert got == {(e["code"], e["path"]) for e in expect["errors"]}


def _ns(iso):
    dt = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)
    return int(dt.timestamp()) * 10**9


def _project(case, dest):
    for sub in ("fixture", "records"):
        src = case / sub
        if src.is_dir():
            shutil.copytree(src, dest / ("records" if sub == "records" else "."), dirs_exist_ok=True)
    meta = read_json(case / "case.json")
    for rel, iso in meta["mtimes"].items():
        os.utime(dest / rel, ns=(_ns(iso), _ns(iso)))
    return meta


def _plan_case(case, tmp_path):
    meta = _project(case, tmp_path)
    structure = tomllib.loads((case / meta["structure"]).read_text(encoding="utf-8"))
    recipe = parse_recipe((case / meta["recipe"]).read_text(encoding="utf-8"), areas=structure)
    omit = set(meta.get("omit", []))  # steps the task filter leaves out of the plan
    task_filter = (lambda step, unit: "omit" if step.name in omit else True) if omit else None
    return build_plan(recipe, tmp_path, structure, steps=meta["steps"], named_inputs=meta["named_inputs"],
                      force=meta["force"], task_filter=task_filter)


@pytest.mark.parametrize("kind", ["plan", "current"])
def test_plan_and_current_cases(kind, tmp_path):
    for case in cases(kind):
        root = tmp_path / kind / case.name
        root.mkdir(parents=True)
        plan = _plan_case(case, root)
        assert plan.to_document() == read_json(case / "expect" / "plan.json"), case.name
        assert plan.digest == (case / "expect" / "digest").read_text().strip(), case.name


@pytest.mark.parametrize("path", cases("input-digest"), ids=ids(cases("input-digest")))
def test_input_digest_cases(path):
    values = read_json(path / "values.json")
    assert input_digest(path / "fixture", values) == read_json(path / "expect.json")["digest"]
