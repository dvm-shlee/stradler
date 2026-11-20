import pytest

from stradler.yamlsubset import YamlSubsetError, loads


def test_block_mapping_sequences_and_comments():
    doc = """\
---
# comment
name: demo   # trailing
list:
  - a
  - "b # not comment"
  - 'it''s'
inline: [x, "y, z", [1, 2]]
nested:
  deep:
    key: value with spaces
items:
- one
- k: v
  k2: v2
-
  - inner
"""
    assert loads(doc) == {
        "name": "demo",
        "list": ["a", "b # not comment", "it's"],
        "inline": ["x", "y, z", ["1", "2"]],
        "nested": {"deep": {"key": "value with spaces"}},
        "items": ["one", {"k": "v", "k2": "v2"}, ["inner"]],
    }


def test_no_implicit_typing():
    d = loads("a: no\nb: 1.10\nc: true\nd: null\ne: ~\nf: 0x1F\ng: 2026-10-29\n")
    assert all(isinstance(v, str) for v in d.values())
    assert d["a"] == "no" and d["b"] == "1.10" and d["d"] == "null"


def test_empty_value_and_document():
    assert loads("") == {}
    assert loads("# only comment\n") == {}
    assert loads("a:\nb: x") == {"a": "", "b": "x"}


def test_double_quote_escapes():
    assert loads(r'a: "x\ny\"z"') == {"a": 'x\ny"z'}


@pytest.mark.parametrize(
    "doc",
    [
        "a: &anchor 1",
        "a: *alias",
        "a: !!str 1",
        "a: !tag x",
        "a: |\n  text",
        "a: >\n  text",
        "a: {x: 1}",
        "a:\n\t- x",
        "a: 1\na: 2",
        "a: 1\n---\nb: 2",
        "%YAML 1.2\na: 1",
        "a: [1, 2",
        "a: b: c",
        'a: "unterminated',
        "a: 1\n  b: 2",
        "- a\nb: 1",
        '  a: "x\\qy"',
    ],
)
def test_rejected(doc):
    with pytest.raises(YamlSubsetError):
        loads(doc)
