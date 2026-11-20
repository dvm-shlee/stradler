import datetime as dt
import tomllib

import pytest

from stradler import tomlw


def rt(d):
    return tomllib.loads(tomlw.dumps(d))


def test_scalars_and_tables_round_trip():
    d = {
        "schema": "structure@1.0.0",
        "n": 3, "neg": -4, "f": 1.5, "t": True, "off": False,
        "s": 'quote " back \\ nl \n tab \t ctl \x01 uni é',
        "arr": [1, 2, 3], "strs": ["a", "b"], "nested": [[1, 2], ["x"]], "empty": [],
        "when": dt.datetime(2026, 10, 29, 12, 0, 0),  # noqa: DTZ001 - TOML local date-time is the format under test
        "areas": {"raw": {"role": "input", "agent": {"search": True}}, "odd key": {"x": 1}},
    }
    assert rt(d) == d


def test_inline_and_array_of_tables():
    d = {"a": [{"x": 1}, {"x": 2, "sub": {"y": "z"}}], "arr": [1]}
    assert rt(d) == d
    assert rt({"v": [{"k": 1}, 5]}) == {"v": [{"k": 1}, 5]}


def test_special_floats():
    out = rt({"a": float("inf"), "b": float("-inf")})
    assert out["a"] == float("inf") and out["b"] == float("-inf")
    assert rt({"n": float("nan")})["n"] != rt({"n": float("nan")})["n"]


def test_quoted_keys():
    assert rt({"a.b": 1}) == {"a.b": 1}


def test_rejects_unwritable():
    with pytest.raises(tomlw.TomlWriteError):
        tomlw.dumps({"a": None})
    with pytest.raises(tomlw.TomlWriteError):
        tomlw.dumps({1: "a"})
    with pytest.raises(tomlw.TomlWriteError):
        tomlw.dumps({"": "a"})
    with pytest.raises(tomlw.TomlWriteError):
        tomlw.dumps([1])


def test_empty():
    assert tomlw.dumps({}) == ""
