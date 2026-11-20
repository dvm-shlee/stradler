"""Fake store add-on (ADDONS.md section 8): ``digest`` reads files below ``settings.root``.

``settings.mode`` is ``ok`` (default), ``unsupported`` (no ``digest``) or ``down`` (backend error).
"""

import hashlib
import json
import os
import sys


def reply(ok, result=None, error=None, code=0):
    doc = {"protocol_version": "1.0", "ok": ok}
    doc.update({"result": result} if ok else {"error": error})
    print(json.dumps(doc))
    sys.exit(code)


req = json.loads(sys.stdin.read())
op, params, settings = req["op"], req["params"], req["settings"]
mode = settings.get("mode", "ok")
if op == "digest" and mode == "ok":
    files = []
    for rel in params["paths"]:
        full = os.path.join(settings["root"], rel)
        if os.path.isfile(full):
            with open(full, "rb") as fh:
                data = fh.read()
            files.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    reply(True, {"files": files})
if op == "digest" and mode == "down":
    reply(False, error={"code": "backend_error", "message": "store is offline"}, code=1)
reply(False, error={"code": "unsupported_operation", "message": op}, code=1)
