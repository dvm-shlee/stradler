import tomllib

import pytest

from stradler.workflow import PlanError, RecipeError, build_plan, parse_recipe

STRUCT = tomllib.loads("""
schema = "structure@1.0.0"
[areas.texts]
role = "input"
path = "texts"
mutability = "mutable"
[areas.work]
role = "output"
path = "work"
mutability = "mutable"
""")

RECIPE = """schema: workflow@1.0.0
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
    command: [tool, "${source}", "${output}"]
    in:
      - as: source
        from: inputs
    out:
      - name: o
        path: work/{stem}.one
  - name: two
    purpose: Second.
    level: file
    container: none
    command: [tool, "${prev}", "${output}"]
    in:
      - as: prev
        from: one
    out:
      - name: o
        path: work/{stem}.two
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / "a.txt").write_text("a")
    (tmp_path / "work").mkdir()
    return tmp_path


def test_missing_fixed_input_is_not_run(root):
    text = RECIPE.replace("  - as: prev\n        from: one\n", "  - as: prev\n        from: fixed\n        path: nope\n")
    r = parse_recipe(text, areas=STRUCT)
    plan = build_plan(r, root, STRUCT)
    (t1, t2) = plan.tasks()
    assert t1.status == "planned" and t2.status == "not-run"
    assert "nope" in t2.reason


def test_chain_rule_marks_followers(root):
    text = RECIPE.replace("    command: [tool, \"${source}\", \"${output}\"]\n    in:\n      - as: source\n        from: inputs\n",
                          "    command: [tool, \"${source}\", \"${output}\"]\n    in:\n      - as: source\n        from: fixed\n        path: nope\n", 1)
    plan = build_plan(parse_recipe(text, areas=STRUCT), root, STRUCT)
    t1, t2 = plan.tasks()
    assert t1.status == "not-run"
    assert t2.status == "not-run" and "own chain" in t2.reason


def test_same_level_steps_share_a_segment(root):
    plan = build_plan(parse_recipe(RECIPE, areas=STRUCT), root, STRUCT)
    assert len(plan.segments) == 1 and plan.segments[0].steps == ["one", "two"]
    assert plan.digest == build_plan(parse_recipe(RECIPE, areas=STRUCT), root, STRUCT).digest


def test_plan_writes_nothing(root):
    before = sorted(p.name for p in root.rglob("*"))
    build_plan(parse_recipe(RECIPE, areas=STRUCT), root, STRUCT)
    assert sorted(p.name for p in root.rglob("*")) == before


def test_unknown_step_and_unsafe_named_input(root):
    r = parse_recipe(RECIPE, areas=STRUCT)
    with pytest.raises(PlanError):
        build_plan(r, root, STRUCT, steps=["nope"])
    with pytest.raises(PlanError):
        build_plan(r, root, STRUCT, named_inputs={"x": "../out"})


def test_levels_of_handler_are_checked():
    with pytest.raises(RecipeError):
        parse_recipe(RECIPE.replace("level: file", "level: session", 1), areas=STRUCT)
    parse_recipe(RECIPE.replace("level: file", "level: session").replace("{stem}", "{session}"), areas=STRUCT,
                 levels=("file", "session", "group"))


def _hooked(root_text, out_path, cmd):
    import dataclasses

    from stradler.workflow import parse_recipe as pr
    rec = pr(root_text)
    st = rec.steps[0]
    st = dataclasses.replace(st, command=tuple(cmd) if isinstance(st.command, tuple) else list(cmd),
                             outputs=tuple(dataclasses.replace(o, path=out_path) for o in st.outputs))
    return dataclasses.replace(rec, steps=(st,) + tuple(rec.steps[1:]) if isinstance(rec.steps, tuple) else [st] + list(rec.steps[1:]))


def test_placeholder_resolver_and_output_namer(root):
    recipe = _hooked(RECIPE, "work/{extra}.one", ["tool", "${source}", "${ns.key}", "${output}"])
    seen = []

    def resolver(name, ctx):
        seen.append((name, ctx["kind"]))
        return {"extra": "E", "ns.key": "K"}.get(name)

    plan = build_plan(recipe, root, STRUCT, placeholder_resolver=resolver,
                      output_namer=lambda step, unit, out, path: path + ".x")
    t = plan.tasks()[0]
    assert t.status == "planned"
    assert t.outputs == {"o": "work/E.one.x"}
    assert t.argv[2] == "K"
    assert ("ns.key", "command") in seen and ("extra", "output") in seen


def test_resolver_none_leaves_unresolved(root):
    recipe = _hooked(RECIPE, "work/{stem}.one", ["tool", "${source}", "${nope.x}"])
    plan = build_plan(recipe, root, STRUCT, placeholder_resolver=lambda n, c: None)
    assert plan.tasks()[0].status == "not-run"


def test_resolver_overrides_builtin(root):
    recipe = _hooked(RECIPE, "work/{stem}.one", ["tool", "${source}", "${output}"])
    plan = build_plan(recipe, root, STRUCT, placeholder_resolver=lambda n, c: "Z" if n == "stem" else None)
    assert plan.tasks()[0].outputs == {"o": "work/Z.one"}


def test_known_placeholder_accepted_by_parse_recipe():
    doc = RECIPE.replace('"${source}", "${output}"]', '"${source}", "${acq.tr}", "${output}"]', 1)
    with pytest.raises(RecipeError):
        parse_recipe(doc)
    rec = parse_recipe(doc, known_placeholder=lambda n: n.startswith("acq."))
    assert rec.name == "t.flow"
    doc2 = RECIPE.replace("work/{stem}.one", "work/{acq}.one", 1)
    with pytest.raises(RecipeError):
        parse_recipe(doc2)
    parse_recipe(doc2, known_placeholder=lambda n: n == "acq")


# -- task_filter, input_resolver, task_hook, condition on an input binding ----------------------

def _two(root):
    (root / "texts" / "b.txt").write_text("b")
    return root


def test_task_filter_leaves_task_out(root):
    _two(root)
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_filter=lambda s, u: not u.id.endswith("b.txt"))
    by = {(t.step, t.unit): t for t in plan.tasks()}
    assert by[("one", "texts/a.txt")].status == "planned"
    t = by[("one", "texts/b.txt")]
    assert (t.status, t.reason) == ("not-run", "excluded by the task filter")
    assert by[("two", "texts/b.txt")].reason == "upstream task in this unit's own chain did not run"


def test_task_filter_per_step_with_text(root):
    seen = []

    def flt(step, unit):
        seen.append((step.name, unit.id))
        return (False, "not applicable here") if step.name == "two" else True

    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_filter=flt)
    one, two = plan.tasks()
    assert one.status == "planned"
    assert (two.status, two.reason) == ("not-run", "not applicable here")
    assert seen == [("one", "texts/a.txt"), ("two", "texts/a.txt")]


def _named_recipe():
    return RECIPE.replace("    in:\n      - as: prev\n        from: one\n",
                          "    in:\n      - as: prev\n        from: one\n      - as: ref\n        from: named\n        name: ref\n", 1
                          ).replace('"${prev}", "${output}"', '"${prev}", "${ref}", "${output}"')


def test_input_resolver_unsafe_named_path(root):
    named = {"ref": "/abs/outside.txt"}
    with pytest.raises(PlanError):
        build_plan(parse_recipe(_named_recipe()), root, STRUCT, named_inputs=named)
    calls = []

    def res(step, unit, name, entry):
        calls.append((step.name, unit.id, name))
        return "texts/a.txt"

    plan = build_plan(parse_recipe(_named_recipe()), root, STRUCT, named_inputs=named, input_resolver=res)
    two = plan.tasks()[1]
    assert two.status == "planned" and two.bindings["ref"] == "texts/a.txt"
    assert calls == [("two", "texts/a.txt", "ref")]


def test_input_resolver_none_keeps_behaviour(root):
    plan = build_plan(parse_recipe(_named_recipe()), root, STRUCT, named_inputs={"ref": "/abs/x"},
                      input_resolver=lambda *a: None)
    two = plan.tasks()[1]
    assert two.status == "not-run" and "unsafe path" in two.reason


def test_input_resolver_exists_flag(root):
    named = {"ref": "/abs/outside.txt"}
    plan = build_plan(parse_recipe(_named_recipe()), root, STRUCT, named_inputs=named,
                      input_resolver=lambda *a: "later/ref.txt")
    assert plan.tasks()[1].status == "not-run"
    plan = build_plan(parse_recipe(_named_recipe()), root, STRUCT, named_inputs=named,
                      input_resolver=lambda *a: ("later/ref.txt", {"exists": False}))
    two = plan.tasks()[1]
    assert two.status == "planned" and two.bindings["ref"] == "later/ref.txt"


def test_input_resolver_step_without_task_in_plan(root):
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, steps=["two"],
                      input_resolver=lambda step, unit, name, entry: ("elsewhere/one.out", {"exists": False}))
    t = plan.tasks()[0]
    assert t.status == "planned" and t.bindings["prev"] == "elsewhere/one.out"
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, steps=["two"])
    assert plan.tasks()[0].status == "not-run"


def test_task_hook_adds_bindings_and_extra(root):
    def hook(task):
        if task.step == "two":
            task.bindings["side"] = "texts/a.txt"
            task.extra["note"] = {"k": 1}
        return task

    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_hook=hook)
    one, two = plan.tasks()
    assert one.extra == {} and "extra" not in one.to_document()
    assert two.to_document()["extra"] == {"note": {"k": 1}}
    assert two.bindings["side"] == "texts/a.txt"
    base = build_plan(parse_recipe(RECIPE), root, STRUCT).tasks()[1]
    assert two.components["inputs"] != base.components["inputs"]


def test_task_hook_may_return_none_and_change_status(root):
    def hook(task):
        if task.step == "one":
            task.status, task.reason = "not-run", "vetoed"

    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_hook=hook)
    assert plan.tasks()[0].reason == "vetoed"


def _cond_recipe():
    return RECIPE.replace(
        "      - as: prev\n        from: one\n",
        "      - as: prev\n        from: one\n      - as: alt\n        from: one\n        select:\n"
        "          condition:\n            exists: prev\n        else:\n          from: fixed\n          path: texts/a.txt\n", 1
    ).replace('"${prev}", "${output}"', '"${prev}", "${alt}", "${output}"')


def test_condition_exists_reads_input_binding(root):
    rec = parse_recipe(_cond_recipe())
    plan = build_plan(rec, root, STRUCT)
    assert plan.tasks()[1].bindings["alt"] == "texts/a.txt"  # work/a.one is not on disk yet
    (root / "work" / "a.one").write_text("x")
    plan = build_plan(rec, root, STRUCT)
    assert plan.tasks()[1].bindings["alt"] == "work/a.one"


def test_finer_level_from_without_select_stays_ref_level_with_all_hooks(root):
    # A step reading a finer-level step without for_each or pick is not defined (3.10); the hooks do not change it.
    rec = RECIPE.replace("    level: file\n    container: none\n    command: [tool, \"${prev}\"",
                         "    level: group\n    container: none\n    command: [tool, \"${prev}\"", 1)
    assert rec != RECIPE
    calls = []
    with pytest.raises(RecipeError) as exc:
        build_plan(parse_recipe(rec), root, STRUCT,
                   task_filter=lambda s, u: calls.append("f") or True,
                   input_resolver=lambda *a: calls.append("r"),
                   task_hook=lambda t: calls.append("h") or t)
    assert "ref.level" in [p.code for p in exc.value.problems]
    assert calls == []


def _plan_ones(root, **kw):
    return {(t.step, t.unit): t for t in build_plan(parse_recipe(RECIPE), root, STRUCT, **kw).tasks()}


def test_task_filter_omit_removes_task_and_keeps_chain(root):
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_filter=lambda s, u: "omit" if s.name == "one" else True)
    assert [t.step for t in plan.tasks()] == ["two"]
    assert plan.segments[0].steps == ["two"]
    assert plan.to_document()["segments"][0]["steps"] == ["two"]
    two = plan.tasks()[0]
    assert two.status == "not-run" and two.reason.startswith("missing input")  # work/a.one is not on disk


def test_task_filter_omit_does_not_cut_the_chain(root):
    (root / "work" / "a.one").write_text("x")
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_filter=lambda s, u: ("omit", "n/a") if s.name == "one" else True)
    two = plan.tasks()[0]
    assert two.status == "planned" and two.bindings["prev"] == "work/a.one"


def test_task_filter_omit_for_some_units_keeps_step_in_segment(root):
    _two(root)
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT,
                      task_filter=lambda s, u: "omit" if u.id.endswith("b.txt") else True)
    assert plan.segments[0].steps == ["one", "two"]
    chains = {c.unit: [t.step for t in c.tasks] for c in plan.segments[0].chains}
    assert chains == {"texts/a.txt": ["one", "two"], "texts/b.txt": []}


def test_task_filter_false_still_kills_chain_after_omit_support(root):
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, task_filter=lambda s, u: s.name != "one")
    assert [t.status for t in plan.tasks()] == ["not-run", "not-run"]


def test_condition_resolver_decides_and_none_keeps_rule(root):
    rec = parse_recipe(_cond_recipe())
    calls = []

    def res(step, unit, name):
        calls.append((step.name, unit.id, name))
        return True if step.name == "two" else None

    plan = build_plan(rec, root, STRUCT, condition_resolver=res)
    assert calls == [("two", "texts/a.txt", "prev")]
    assert plan.tasks()[1].bindings["alt"] == "work/a.one"  # condition true although a.one is not on disk
    (root / "work" / "a.one").write_text("x")
    plan = build_plan(rec, root, STRUCT, condition_resolver=lambda s, u, n: False)
    assert plan.tasks()[1].bindings["alt"] == "texts/a.txt"  # forced false takes the else entry
    plan = build_plan(rec, root, STRUCT, condition_resolver=lambda s, u, n: None)
    assert plan.tasks()[1].bindings["alt"] == "work/a.one"


def test_condition_resolver_sees_child_units(tmp_path):
    (tmp_path / "texts").mkdir()
    for n in ("a", "b"):
        (tmp_path / "texts" / f"{n}.txt").write_text(n)
    rec = parse_recipe(RECIPE + """  - name: agg
    purpose: Join.
    level: group
    container: none
    command: [tool, "${all}", "${output}"]
    in:
      - as: all
        from: one
        select:
          for_each: file
          condition:
            exists: o
    out:
      - name: t
        path: work/total.txt
""")
    seen = []

    def res(step, unit, name):
        seen.append((step.name, unit.id, name))
        return unit.id.endswith("a.txt")

    plan = build_plan(rec, tmp_path, STRUCT, condition_resolver=res)
    agg = next(t for t in plan.tasks() if t.step == "agg")
    assert ("agg", "texts/a.txt", "o") in seen and ("agg", "texts/b.txt", "o") in seen
    assert agg.bindings["all"] == ["work/a.one"] and (agg.included, agg.expected) == (1, 2)


def test_else_entry_honours_its_own_select(tmp_path):
    (tmp_path / "texts").mkdir()
    (tmp_path / "work").mkdir()
    for n in ("a", "b"):
        (tmp_path / "texts" / f"{n}.txt").write_text(n)
    rec = parse_recipe(RECIPE + """  - name: mid
    purpose: Group level first.
    level: group
    container: none
    command: [tool, "${x}", "${output}"]
    in:
      - as: x
        from: one
        select:
          for_each: file
    out:
      - name: o
        path: work/mid.txt
  - name: agg
    purpose: Fallback that is itself a join.
    level: group
    container: none
    command: [tool, "${basis}", "${output}"]
    in:
      - as: basis
        from: mid
        select:
          condition:
            exists: o
        else:
          from: one
          select:
            for_each: file
    out:
      - name: t
        path: work/total.txt
""")
    plan = build_plan(rec, tmp_path, STRUCT)
    agg = next(t for t in plan.tasks() if t.step == "agg")
    assert agg.bindings["basis"] == ["work/a.one", "work/b.one"]
    (tmp_path / "work" / "mid.txt").write_text("m")
    plan = build_plan(rec, tmp_path, STRUCT)
    assert next(t for t in plan.tasks() if t.step == "agg").bindings["basis"] == "work/mid.txt"


def _record(root, version, **extra):
    import json
    d = root / "records" / "20261001T000000Z-00000001" / "one" / "texts" / "a.txt"
    d.mkdir(parents=True)
    (d / "task.json").write_text(json.dumps({"record_version": version, "status": "ok", **extra}))


def test_record_version_above_supported_raises_without_reader(root):
    (root / "work" / "a.one").write_text("x")
    _record(root, 2)
    with pytest.raises(PlanError):
        build_plan(parse_recipe(RECIPE), root, STRUCT, records_dir="records")


def test_record_reader_replaces_read_and_version_check(root):
    import json
    (root / "work" / "a.one").write_text("x")
    _record(root, 2, stradler_record_version=1)
    paths = []

    def reader(path):
        paths.append(path)
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        rec["record_version"] = rec.pop("stradler_record_version", rec["record_version"])
        return rec

    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, records_dir="records", record_reader=reader)
    one = plan.tasks()[0]
    assert len(paths) == 1 and paths[0].endswith("one/texts/a.txt/task.json")
    assert (one.status, one.reason) == ("planned", "inputs changed")  # an ok record was found and compared


def test_record_reader_none_ignores_the_file(root):
    (root / "work" / "a.one").write_text("x")
    _record(root, 2)
    plan = build_plan(parse_recipe(RECIPE), root, STRUCT, records_dir="records", record_reader=lambda p: None)
    assert plan.tasks()[0].reason == "no ok record"


def test_input_resolver_for_fixed_missing_and_named_not_given(root):
    rec = RECIPE.replace("      - as: prev\n        from: one\n",
                         "      - as: prev\n        from: one\n      - as: fx\n        from: fixed\n        path: defaults/x.txt\n"
                         "      - as: nm\n        from: named\n        name: extra\n", 1)
    rec = rec.replace('"${prev}", "${output}"', '"${prev}", "${fx}", "${nm}", "${output}"')
    (root / "work" / "a.one").write_text("x")
    plan = build_plan(parse_recipe(rec), root, STRUCT)
    assert plan.tasks()[1].reason.startswith("missing input 'fx'")
    seen = []

    def res(step, unit, name, entry):
        seen.append(name)
        return (f"hooked/{name}", {"exists": False})

    two = build_plan(parse_recipe(rec), root, STRUCT, input_resolver=res).tasks()[1]
    assert seen == ["fx", "nm"] and two.bindings["fx"] == "hooked/fx" and two.bindings["nm"] == "hooked/nm"


class DictFS:
    """A fs that answers from a set of paths; nothing exists on disk."""

    def __init__(self, files):
        self.files = set(files)
        self.dirs = {"."} | {p.rsplit("/", 1)[0] for p in self.files if "/" in p}
        for d in list(self.dirs):
            while "/" in d:
                d = d.rsplit("/", 1)[0]
                self.dirs.add(d)
        self.calls = []

    def exists(self, rel):
        self.calls.append(("exists", rel))
        return rel in self.files or rel in self.dirs

    def is_file(self, rel):
        self.calls.append(("is_file", rel))
        return rel in self.files

    def is_dir(self, rel):
        self.calls.append(("is_dir", rel))
        return rel in self.dirs

    def lexists(self, rel):
        return self.exists(rel)

    def is_link(self, rel):
        return False

    def stat(self, rel):
        self.calls.append(("stat", rel))
        return getattr(self, "stats", {}).get(rel)

    def list_dir(self, rel):
        self.calls.append(("list_dir", rel))
        pre = "" if rel == "." else rel + "/"
        return sorted({p[len(pre):].split("/")[0] for p in self.files if p.startswith(pre)})


def test_dict_fs_plans_and_finds_current_tasks_without_files(tmp_path):
    from stradler.workflow import Unit, UnitSet
    unit = Unit("file", "texts/a.txt", ("texts/a.txt",), {"file": "texts/a.txt"})
    units = UnitSet(["file"], [unit])
    rec = "records/20261001T000000Z-00000001/one/texts/a.txt/task.json"
    fs = DictFS(["texts/a.txt", "work/a.one", rec])
    seen = []

    def reader(path):
        seen.append(path)
        return {"record_version": 1, "status": "ok"}

    recipe = parse_recipe(RECIPE, areas=STRUCT)
    plan = build_plan(recipe, tmp_path / "nowhere", STRUCT, units=units, records_dir="records", fs=fs,
                      record_reader=reader)
    by = {t.step: t for t in plan.tasks()}
    assert by["one"].status != "not-run" and by["two"].status != "not-run"  # inputs and outputs found via fs
    assert seen and ("list_dir", "records") in fs.calls and ("is_file", rec) in fs.calls
    assert not (tmp_path / "nowhere").exists()


def test_local_fs_default_matches_explicit(root):
    from stradler.workflow import LocalFS
    (root / "work" / "a.one").write_text("x")
    r = parse_recipe(RECIPE, areas=STRUCT)
    assert build_plan(r, root, STRUCT).digest == build_plan(r, root, STRUCT, fs=LocalFS(root)).digest


class StatFS(DictFS):
    """DictFS that also holds output stats and record texts."""

    def __init__(self, files, stats=None, texts=None):
        super().__init__(files)
        self.stats, self.texts = stats or {}, texts or {}


    def stat(self, rel):
        self.calls.append(("stat", rel))
        return self.stats.get(rel)

    def read_text(self, rel):
        self.calls.append(("read_text", rel))
        return self.texts[rel]


def test_plan_current_from_dict_fs(tmp_path):
    import json

    from stradler.workflow import Unit, UnitSet
    unit = Unit("file", "texts/a.txt", ("texts/a.txt",), {"file": "texts/a.txt"})
    units = UnitSet(["file"], [unit])
    recipe = parse_recipe(RECIPE, areas=STRUCT)
    base = tmp_path / "nowhere"
    rec_dir = "records/20261001T000000Z-00000001"
    probe = build_plan(recipe, base, STRUCT, units=units, fs=DictFS(["texts/a.txt"]))
    stats = {"work/a.one": {"size": 3, "mtime_ns": 7}}
    texts, files = {}, ["texts/a.txt", "work/a.one"]
    for t in probe.tasks():
        if t.step == "one":
            doc = {"record_version": 1, "status": "ok", "input_components": t.components,
                   "output_stats": {n: stats.get(p) for n, p in t.outputs.items()}}
            path = f"{rec_dir}/one/texts/a.txt/task.json"
            texts[path] = json.dumps(doc)
            files.append(path)
    fs = StatFS(files, stats, texts)
    plan = build_plan(recipe, base, STRUCT, units=units, records_dir="records", fs=fs)
    one = next(t for t in plan.tasks() if t.step == "one")
    assert one.status == "skip" and one.reason == "task is current"
    assert ("stat", "work/a.one") in fs.calls and any(c[0] == "read_text" for c in fs.calls)
    stats["work/a.one"] = {"size": 4, "mtime_ns": 7}
    again = build_plan(recipe, base, STRUCT, units=units, records_dir="records", fs=fs)
    assert next(t for t in again.tasks() if t.step == "one").status == "planned"
    assert not base.exists()
