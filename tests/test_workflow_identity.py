import os

import pytest

from stradler.workflow import (
    canonical_json,
    digest_of,
    input_components,
    input_digest,
    output_stats,
    sha256_digest,
)


def test_canonical_rejects_floats_and_large_ints():
    with pytest.raises(ValueError):
        canonical_json({"a": 1.5})
    with pytest.raises(ValueError):
        canonical_json(2**53 + 1)
    with pytest.raises(ValueError):
        canonical_json({1: "a"})


def test_canonical_key_order_is_bytewise():
    assert canonical_json({"b": 1, "B": 2, "é": 3}) == '{"B":2,"b":1,"é":3}'


def test_digest_form():
    d = digest_of({"a": 1})
    assert d == sha256_digest(b'{"a":1}') and d.startswith("sha256:") and len(d) == 71


def test_input_digest_depends_on_content_and_length_prefix(tmp_path):
    (tmp_path / "a").write_text("xy")
    (tmp_path / "b").write_text("z")
    d1 = input_digest(tmp_path, {"s": ["a", "b"]})
    assert d1 == input_digest(tmp_path, {"t": ["b", "a", "a"]})
    (tmp_path / "a").write_text("x")
    (tmp_path / "b").write_text("yz")
    assert input_digest(tmp_path, {"s": ["a", "b"]}) != d1


def test_input_digest_missing_differs_from_empty(tmp_path):
    (tmp_path / "e").write_text("")
    assert input_digest(tmp_path, {"s": "e"}) != input_digest(tmp_path, {"s": "nothing"})


def test_output_stats_file_and_directory(tmp_path):
    (tmp_path / "f").write_text("abc")
    assert output_stats(tmp_path, "f")["size"] == 3
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x").write_text("12")
    (tmp_path / "d" / "y").write_text("345")
    os.utime(tmp_path / "d" / "y", ns=(2_000_000_000 * 10**9, 2_000_000_000 * 10**9))
    st = output_stats(tmp_path, "d")
    assert st["size"] == 5 and st["mtime_ns"] == 2_000_000_000 * 10**9
    assert output_stats(tmp_path, "none") is None


def test_input_components_shape():
    c = input_components("sha256:" + "a" * 64, ["x"], None, ("n", "f", "1.0.0"))
    assert set(c) == {"inputs", "command", "container", "workflow"}
    assert c["container"] == sha256_digest(b"")
