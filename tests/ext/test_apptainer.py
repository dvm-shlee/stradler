import hashlib
import io
import json
import os
import stat
import subprocess
import sys

import pytest

from stradler.ext import apptainer

FAKE = """#!{py}
import json, sys
args = sys.argv[1:]
if args[0] == "build":
    open(args[1], "wb").write(b"fake image")
elif args[0] == "inspect":
    print(json.dumps({{"data": {{"attributes": {{"labels": {{"org.example.name": "x", "n": 3}}}}}}}}))
"""


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "fake-apptainer"
    path.write_text(FAKE.format(py=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def call(op, params=None, settings=None, version="1.0", kind="container_runtime"):
    req = {"protocol_version": version, "kind": kind, "op": op, "settings": settings or {}, "params": params or {}}
    out = io.StringIO()
    code = apptainer.main(stdin=io.StringIO(json.dumps(req)), stdout=out)
    return code, json.loads(out.getvalue())


def test_capabilities():
    code, doc = call("capabilities")
    r = doc["result"]
    assert code == 0 and doc["ok"]
    assert r["kind"] == "container_runtime" and r["image_formats"] == ["sif", "docker"] and r["platforms"] == ["linux"]
    assert r["recipe_formats"] == ["apptainer-def", "docker"] and "sha256" in r["digest_algorithms"]


def test_wrap_shape_and_modes():
    binds = [{"source": "/a/in", "target": "/in", "mode": "ro"}, {"source": "/a/out", "target": "/out", "mode": "rw"}]
    code, doc = call("wrap", {"argv": ["ls", "-l"], "image": "/img.sif", "binds": binds, "workdir": "/out"})
    assert code == 0
    assert doc["result"]["argv"] == ["apptainer", "exec", "--containall", "--bind", "/a/in:/in:ro", "--bind",
                                     "/a/out:/out", "--pwd", "/out", "/img.sif", "ls", "-l"]


def test_wrap_no_binds_adds_none():
    _, doc = call("wrap", {"argv": ["x"], "image": "/i.sif", "binds": []}, {"apptainer_command": "/bin/apt"})
    assert doc["result"]["argv"] == ["/bin/apt", "exec", "--containall", "/i.sif", "x"]


@pytest.mark.parametrize("bind", [
    {"source": "rel", "target": "/t", "mode": "ro"},
    {"source": "/a/../b", "target": "/t", "mode": "ro"},
    {"source": "/a", "target": "/t", "mode": "x"},
])
def test_wrap_refuses_bad_bind(bind):
    code, doc = call("wrap", {"argv": ["x"], "image": "/i.sif", "binds": [bind]})
    assert doc["ok"] is False and code in (1, 2)
    assert doc["error"]["code"] in ("refused", "invalid_request")


def test_digest_of_small_file(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"abc")
    assert apptainer.sha256_file(str(f)) == "sha256:" + hashlib.sha256(b"abc").hexdigest()


def test_build_and_inspect(tmp_path, fake):
    recipe = tmp_path / "r.def"
    recipe.write_text("Bootstrap: localimage\n")
    out = tmp_path / "img.sif"
    code, doc = call("build", {"recipe": str(recipe), "format": "apptainer-def", "output": str(out)},
                     {"apptainer_command": fake})
    assert code == 0, doc
    digest = "sha256:" + hashlib.sha256(b"fake image").hexdigest()
    assert doc["result"] == {"image": str(out), "format": "sif", "digest": digest}
    code, doc = call("inspect", {"image": str(out)}, {"apptainer_command": fake})
    r = doc["result"]
    assert code == 0 and r["size_bytes"] == 10 and r["labels"] == {"org.example.name": "x", "n": "3"}
    assert r["digest"] == digest and r["format"] == "sif"


def test_inspect_errors(tmp_path, fake):
    _, doc = call("inspect", {"image": str(tmp_path / "none")}, {"apptainer_command": fake})
    assert doc["error"]["code"] == "not_found"
    _, doc = call("inspect", {"image": str(tmp_path)}, {"apptainer_command": fake})
    assert doc["error"]["code"] == "refused"


def test_backend_errors(tmp_path):
    img = tmp_path / "i.sif"
    img.write_bytes(b"x")
    _, doc = call("inspect", {"image": str(img)}, {"apptainer_command": str(tmp_path / "missing")})
    assert doc["error"]["code"] == "backend_error"
    bad = tmp_path / "bad"
    bad.write_text("#!/bin/sh\nexit 5\n")
    bad.chmod(0o755)
    code, doc = call("inspect", {"image": str(img)}, {"apptainer_command": str(bad)})
    assert code == 1 and doc["error"]["details"]["exit_code"] == 5


def test_error_envelope():
    code, doc = call("capabilities", version="2.0")
    assert code == 3 and doc["error"]["code"] == "unsupported_version"
    code, doc = call("submit")
    assert code == 1 and doc["error"]["code"] == "unsupported_operation"
    code, doc = call("inspect", {"image": "/x", "extra": 1})
    assert code == 2 and doc["error"]["code"] == "invalid_request"
    code, doc = call("capabilities", kind="scheduler")
    assert code == 2
    out = io.StringIO()
    assert apptainer.main(stdin=io.StringIO("not json"), stdout=out) == 2
    assert json.loads(out.getvalue())["error"]["code"] == "invalid_request"


def test_subprocess_entry_point():
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
    req = json.dumps({"protocol_version": "1.0", "kind": "container_runtime", "op": "capabilities", "params": {}})
    p = subprocess.run([sys.executable, "-m", "stradler.ext.apptainer"], input=req, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=src), check=False)
    assert p.returncode == 0 and json.loads(p.stdout)["ok"]


ARGV_FAKE = """#!{py}
import json, sys
args = sys.argv[1:]
open(args[-2] + ".argv", "w").write(json.dumps(args))
open(args[-2], "wb").write(b"fake image")
"""


@pytest.fixture
def argv_fake(tmp_path):
    path = tmp_path / "argv-apptainer"
    path.write_text(ARGV_FAKE.format(py=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _build(tmp_path, argv_fake, **extra):
    recipe = tmp_path / "r.def"
    recipe.write_text("Bootstrap: localimage\n")
    out = tmp_path / "img.sif"
    params = {"recipe": str(recipe), "format": "apptainer-def", "output": str(out), **extra}
    code, doc = call("build", params, {"apptainer_command": argv_fake})
    return code, doc, out, recipe


@pytest.mark.parametrize("extra,flags", [
    ({}, []),
    ({"privilege": "default"}, []),
    ({"privilege": "fakeroot"}, ["--fakeroot"]),
    ({"privilege": "ignore-fakeroot"}, ["--ignore-fakeroot-command"]),
])
def test_build_privilege_argv(tmp_path, argv_fake, extra, flags):
    code, doc, out, recipe = _build(tmp_path, argv_fake, **extra)
    assert code == 0 and doc["ok"]
    with open(str(out) + ".argv") as fh:
        argv = json.load(fh)
    assert argv == ["build"] + flags + [str(out), str(recipe)]


@pytest.mark.parametrize("value", ["root", "", None, 1])
def test_build_privilege_refused(tmp_path, argv_fake, value):
    code, doc, out, _ = _build(tmp_path, argv_fake, privilege=value)
    assert code == 1 and doc["ok"] is False and doc["error"]["code"] == "refused"
    assert not out.exists()


def test_capabilities_privilege_listed():
    _, doc = call("capabilities")
    assert doc["result"]["build_privileges"] == ["default", "fakeroot", "ignore-fakeroot"]


DIGEST = "a" * 64
REF = f"docker://example/tools@sha256:{DIGEST}"


def test_docker_inspect_offline_pinned(tmp_path):
    code, doc = call("inspect", {"image": REF}, {"apptainer_command": str(tmp_path / "must-not-run")})
    assert code == 0 and doc["result"]["format"] == "docker" and doc["result"]["digest"] == "sha256:" + DIGEST


@pytest.mark.parametrize("op", ["inspect", "wrap"])
@pytest.mark.parametrize("ref", ["docker://example/tools:latest", "docker://example/tools",
                                 "docker://example/tools@sha256:abc"])
def test_docker_unpinned_refused(op, ref):
    params = {"image": ref} if op == "inspect" else {"image": ref, "argv": ["x"]}
    code, doc = call(op, params)
    assert code == 1 and doc["error"]["code"] == "refused" and ref in doc["error"]["message"]


def test_docker_wrap_binds():
    binds = [{"source": "/a", "target": "/in", "mode": "ro"}]
    _, doc = call("wrap", {"image": REF, "argv": ["ls"], "binds": binds, "workdir": "/in"})
    assert doc["result"]["argv"] == ["apptainer", "exec", "--containall", "--bind", "/a:/in:ro", "--pwd", "/in",
                                     REF, "ls"]


def test_docker_build_pull_to_sif(tmp_path, argv_fake):
    out = tmp_path / "pulled.sif"
    code, doc = call("build", {"recipe": REF, "format": "docker", "output": str(out)}, {"apptainer_command": argv_fake})
    assert code == 0, doc
    resolved = "sha256:" + hashlib.sha256(b"fake image").hexdigest()
    assert doc["result"] == {"image": str(out), "format": "sif", "digest": resolved,
                             "pin_digest": "sha256:" + DIGEST, "resolved_digest": resolved}
    with open(str(out) + ".argv") as fh:
        assert json.load(fh) == ["build", str(out), REF]


def test_docker_build_unpinned_refused(tmp_path, argv_fake):
    out = tmp_path / "x.sif"
    code, doc = call("build", {"recipe": "docker://example/tools:1", "format": "docker", "output": str(out)},
                     {"apptainer_command": argv_fake})
    assert code == 1 and doc["error"]["code"] == "refused" and not out.exists()
