"""Fake container runtime add-on (ADDONS.md section 4): ``wrap`` prefixes the vector with ``env FAKE_RUNTIME=1``."""

import hashlib
import json
import sys

CAPS = {
    "kind": "container_runtime", "version": "0.1.0", "recipe_formats": ["example-def"], "image_formats": ["sif"],
    "platforms": ["any"], "digest_algorithms": ["sha256"], "bind_modes": ["ro", "rw"], "build_offline": True,
}


def reply(ok, result=None, error=None, code=0):
    doc = {"protocol_version": "1.0", "ok": ok}
    doc.update({"result": result} if ok else {"error": error})
    print(json.dumps(doc))
    sys.exit(code)


req = json.loads(sys.stdin.read())
op, params = req["op"], req["params"]
caps = dict(CAPS, **req["settings"].get("caps", {}))
if op == "capabilities":
    reply(True, caps)
if op == "inspect":
    with open(params["image"], "rb") as fh:
        data = fh.read()
    reply(True, {"format": req["settings"].get("format", "sif"), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                 "size_bytes": len(data), "labels": {}})
if op == "wrap":
    reply(True, {"argv": ["env", "FAKE_RUNTIME=1"] + params["argv"]})
reply(False, error={"code": "unsupported_operation", "message": op}, code=1)
