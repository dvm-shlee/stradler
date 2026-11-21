"""Format guarantees: a minimal project validates with the bundled schemas; the wheel ships them."""

import filecmp
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from stradler.cli import main

ROOT = Path(__file__).resolve().parent.parent


def make_minimal(root: Path) -> Path:
    """Write stradler.json, config@1.0.0 and structure@1.1.0 only (no template.json, no .stradler/schema/)."""
    cd = root / ".stradler"
    cd.mkdir(parents=True)
    (root / "stradler.json").write_text(json.dumps(
        {"marker": "stradler-project", "spec_version": "1.0.0", "project_id": "min", "config_dir": ".stradler"}))
    (cd / "config.toml").write_text('schema = "config@1.0.0"\n')
    (cd / "structure.toml").write_text(
        'schema = "structure@1.1.0"\n\n[areas.code]\nrole = "code"\npath = "code"\nmutability = "mutable"\n')
    return root


def test_minimal_project_validates(tmp_path, capsys):
    root = make_minimal(tmp_path / "min")
    assert not (root / ".stradler" / "template.json").exists()
    assert not (root / ".stradler" / "schema").exists()
    assert main(["validate", str(root)]) == 0
    assert main(["validate", "--json", str(root)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1])["problems"] == []


def test_wheel_contains_bundled_schemas(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not available")
    out = tmp_path / "dist"
    r = subprocess.run([uv, "build", "--wheel", "--out-dir", str(out), str(ROOT)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    (wheel,) = out.glob("stradler-*.whl")
    src = {p.name: p for p in (ROOT / "schemas").glob("*.json")}
    assert src
    with zipfile.ZipFile(wheel) as z:
        names = {n.split("/", 2)[2]: n for n in z.namelist() if n.startswith("stradler/schemas/") and n.endswith(".json")}
        assert set(names) == set(src)
        ex = tmp_path / "x"
        z.extractall(ex)
    for name, p in src.items():
        assert filecmp.cmp(p, ex / "stradler" / "schemas" / name, shallow=False), name
