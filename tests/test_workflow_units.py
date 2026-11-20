import pytest

from stradler.workflow import (
    DefaultHandler,
    InputsBlock,
    UnitsError,
    check_unit_id,
    task_record_dir,
)
from stradler.workflow.units import glob_regex


def make(tmp_path, names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def test_default_handler_units_and_order(tmp_path):
    make(tmp_path, ["d/b.txt", "d/a.txt", "d/sub/c.txt"])
    us = DefaultHandler().discover(tmp_path, InputsBlock("d"))
    assert [u.id for u in us.units("file")] == ["d/a.txt", "d/b.txt", "d/sub/c.txt"]
    (group,) = us.units("group")
    assert group.id == "group" and group.files == ("d/a.txt", "d/b.txt", "d/sub/c.txt")
    assert [u.id for u in us.children(group, "file")] == [u.id for u in us.units("file")]


def test_default_handler_filter(tmp_path):
    make(tmp_path, ["d/a.txt", "d/b.txt", "d/c.md", "d/sub/e.txt"])
    us = DefaultHandler().discover(tmp_path, InputsBlock("d", {"include": ["*.txt"], "exclude": ["b*"]}))
    assert [u.id for u in us.units("file")] == ["d/a.txt"]
    us = DefaultHandler().discover(tmp_path, InputsBlock("d", {"include": ["**/*.txt"]}))
    assert len(us.units("file")) == 3


def test_default_handler_refusals(tmp_path):
    make(tmp_path, ["d/a.txt"])
    with pytest.raises(UnitsError):
        DefaultHandler().discover(tmp_path, InputsBlock("d", {"tag": "x"}))
    with pytest.raises(UnitsError):
        DefaultHandler().discover(tmp_path, InputsBlock("d", {}, {"session": {}}))
    with pytest.raises(UnitsError):
        DefaultHandler().discover(tmp_path, InputsBlock("nothing"))


def test_glob_regex():
    assert glob_regex("*.txt").match("a.txt") and not glob_regex("*.txt").match("s/a.txt")
    assert glob_regex("**/a").match("a") and glob_regex("**/a").match("x/y/a")


def test_unit_identifier_rules():
    check_unit_id("a/b")
    for bad in ("", "/a", "a/../b", "./a", "a//b"):
        with pytest.raises(ValueError):
            check_unit_id(bad)
    assert task_record_dir("records/r1/", "count", "texts/a.txt") == "records/r1/count/texts/a.txt"
