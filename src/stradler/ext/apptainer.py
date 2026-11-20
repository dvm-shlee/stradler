"""Apptainer container runtime add-on (docs/ADDONS.md section 4).

One JSON request on standard input, one JSON response on standard output. Standard library only; it imports nothing
from the stradler core. The apptainer command is ``settings.apptainer_command`` (default ``apptainer``).
"""

import hashlib
import json
import os
import re
import subprocess
import sys

PROTOCOL_VERSION = "1.0"
VERSION = "0.1.0"
KIND = "container_runtime"
EXIT = {"invalid_request": 2, "unsupported_version": 3, "internal": 4}
PARAMS = {
    "capabilities": set(),
    "build": {"recipe", "format", "output", "privilege"},
    "inspect": {"image"},
    "wrap": {"argv", "image", "binds", "workdir"},
}
RECIPE_FORMATS = ["apptainer-def", "docker"]
IMAGE_FORMATS = ["sif", "docker"]
DOCKER_PIN = re.compile(r"^docker://[^\s@]+@(sha256:[0-9a-f]{64})$")
BIND_MODES = ["ro", "rw"]
BUILD_PRIVILEGES = {"default": [], "fakeroot": ["--fakeroot"], "ignore-fakeroot": ["--ignore-fakeroot-command"]}


class AddonError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def capabilities():
    return {"kind": KIND, "version": VERSION, "recipe_formats": list(RECIPE_FORMATS),
            "image_formats": list(IMAGE_FORMATS), "platforms": ["linux"], "digest_algorithms": ["sha256"],
            "bind_modes": list(BIND_MODES), "build_privileges": list(BUILD_PRIVILEGES),
            "build_offline": False}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def _command(settings):
    cmd = settings.get("apptainer_command", "apptainer")
    if not isinstance(cmd, str) or not cmd:
        raise AddonError("invalid_request", "settings.apptainer_command must be a non-empty string")
    return cmd


def _absolute(value, name):
    if not isinstance(value, str) or not os.path.isabs(value):
        raise AddonError("invalid_request", f"{name} must be an absolute path")
    return value


def _docker_digest(ref):
    """Return the sha256 pin of a docker reference; refuse an unpinned one, naming the container."""
    m = DOCKER_PIN.match(ref) if isinstance(ref, str) else None
    if not m:
        raise AddonError("refused", f"docker container {ref} is not pinned by digest "
                         "(need docker://<name>@sha256:<64 hex>)")
    return m.group(1)


def _is_docker(value):
    return isinstance(value, str) and value.startswith("docker://")


def _run(cmd, args):
    try:
        proc = subprocess.run([cmd] + args, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        raise AddonError("backend_error", f"apptainer command not found: {cmd}", {"command": cmd})
    except OSError as exc:
        raise AddonError("backend_error", f"apptainer command failed to start: {exc}", {"command": cmd})
    if proc.returncode != 0:
        raise AddonError("backend_error", f"apptainer exited with code {proc.returncode}",
                         {"exit_code": proc.returncode, "stderr": proc.stderr[-2000:]})
    return proc.stdout


def _build_docker(settings, params):
    ref, output = params["recipe"], _absolute(params["output"], "output")
    pin = _docker_digest(ref)
    privilege = params.get("privilege", "default")
    if not isinstance(privilege, str) or privilege not in BUILD_PRIVILEGES:
        raise AddonError("refused", f"build privilege not supported: {privilege}")
    _run(_command(settings), ["build"] + BUILD_PRIVILEGES[privilege] + [output, ref])
    if not os.path.isfile(output):
        raise AddonError("backend_error", f"apptainer build wrote no image: {output}")
    resolved = sha256_file(output)
    return {"image": output, "format": "sif", "digest": resolved, "pin_digest": pin, "resolved_digest": resolved}


def build(settings, params):
    if params["format"] == "docker":
        return _build_docker(settings, params)
    recipe = _absolute(params["recipe"], "recipe")
    output = _absolute(params["output"], "output")
    if params["format"] not in RECIPE_FORMATS:
        raise AddonError("refused", f"recipe format not supported: {params['format']}")
    if not os.path.isfile(recipe):
        raise AddonError("not_found", f"recipe not found: {recipe}")
    privilege = params.get("privilege", "default")
    if not isinstance(privilege, str) or privilege not in BUILD_PRIVILEGES:
        raise AddonError("refused", f"build privilege not supported: {privilege}")
    _run(_command(settings), ["build"] + BUILD_PRIVILEGES[privilege] + [output, recipe])
    if not os.path.isfile(output):
        raise AddonError("backend_error", f"apptainer build wrote no image: {output}")
    return {"image": output, "format": "sif", "digest": sha256_file(output)}


def _labels(text):
    try:
        doc = json.loads(text)
    except ValueError:
        raise AddonError("backend_error", "apptainer inspect output is not JSON")
    labels = {}
    if isinstance(doc, dict):
        data = doc.get("data")
        attrs = data.get("attributes") if isinstance(data, dict) else None
        found = attrs.get("labels") if isinstance(attrs, dict) else None
        if isinstance(found, dict):
            labels = found
    return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in labels.items()}


def inspect(settings, params):
    if _is_docker(params["image"]):
        return {"format": "docker", "digest": _docker_digest(params["image"]), "size_bytes": None, "labels": {}}
    image = _absolute(params["image"], "image")
    if os.path.isdir(image):
        raise AddonError("refused", f"a directory image has no digest: {image}")
    if not os.path.isfile(image):
        raise AddonError("not_found", f"image not found: {image}")
    out = _run(_command(settings), ["inspect", "--json", image])
    return {"format": "sif", "digest": sha256_file(image), "size_bytes": os.path.getsize(image),
            "labels": _labels(out)}


def wrap(settings, params):
    if _is_docker(params["image"]):
        _docker_digest(params["image"])
        image = params["image"]
    else:
        image = _absolute(params["image"], "image")
    argv = params["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise AddonError("invalid_request", "argv must be a non-empty list of strings")
    binds = params.get("binds") or []
    workdir = params.get("workdir")
    out = [_command(settings), "exec", "--containall"]
    for b in binds:
        if not isinstance(b, dict) or not {"source", "target", "mode"} <= set(b):
            raise AddonError("invalid_request", "each bind needs source, target and mode")
        src, dst, mode = b["source"], b["target"], b["mode"]
        _absolute(src, "bind source")
        _absolute(dst, "bind target")
        if ".." in src.split("/"):
            raise AddonError("refused", f"bind source holds a '..' segment: {src}")
        if mode not in BIND_MODES:
            raise AddonError("refused", f"bind mode not supported: {mode}")
        if ":" in src or ":" in dst:
            raise AddonError("refused", "bind paths must not contain ':'")
        out += ["--bind", f"{src}:{dst}:ro" if mode == "ro" else f"{src}:{dst}"]
    if workdir is not None:
        out += ["--pwd", _absolute(workdir, "workdir")]
    return {"argv": out + [image] + argv}


OPS = {"build": build, "inspect": inspect, "wrap": wrap}


def handle(request):
    """Return the result for a parsed request, or raise AddonError."""
    if not isinstance(request, dict):
        raise AddonError("invalid_request", "request must be a JSON object")
    version = request.get("protocol_version")
    if not isinstance(version, str) or not version.split(".")[0].isdigit():
        raise AddonError("invalid_request", "protocol_version missing or malformed")
    if version.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
        raise AddonError("unsupported_version", f"protocol version {version} is not supported")
    if request.get("kind") != KIND:
        raise AddonError("invalid_request", f"kind must be {KIND}")
    op, settings, params = request.get("op"), request.get("settings", {}), request.get("params", {})
    if not isinstance(settings, dict) or not isinstance(params, dict):
        raise AddonError("invalid_request", "settings and params must be objects")
    if op not in PARAMS:
        raise AddonError("unsupported_operation", f"operation not implemented: {op}")
    unknown = set(params) - PARAMS[op]
    if unknown:
        raise AddonError("invalid_request", f"unknown params: {sorted(unknown)}")
    optional = {"wrap": {"binds", "workdir"}, "build": {"privilege"}}.get(op, set())
    required = PARAMS[op] - optional
    missing = required - set(params)
    if missing:
        raise AddonError("invalid_request", f"missing params: {sorted(missing)}")
    if op == "capabilities":
        return capabilities()
    return OPS[op](settings, params)


def main(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        try:
            request = json.loads(stdin.read())
        except ValueError:
            raise AddonError("invalid_request", "request is not valid JSON")
        doc, code = {"protocol_version": PROTOCOL_VERSION, "ok": True, "result": handle(request)}, 0
    except AddonError as exc:
        doc = {"protocol_version": PROTOCOL_VERSION, "ok": False,
               "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        code = EXIT.get(exc.code, 1)
    except Exception as exc:  # noqa: BLE001 - the protocol reports bugs as internal
        doc = {"protocol_version": PROTOCOL_VERSION, "ok": False,
               "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}", "details": {}}}
        code = 4
    stdout.write(json.dumps(doc) + "\n")
    stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
