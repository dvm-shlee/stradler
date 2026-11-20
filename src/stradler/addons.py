"""Client of the add-on protocols (ADDONS.md sections 1 and 5): discovery, one request per process, plan check."""

from __future__ import annotations

import json
import os
import platform as _platform
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .project import CONFIG_DIR

__all__ = [
    "ERROR_CODES",
    "PROTOCOL_VERSION",
    "Addon",
    "AddonError",
    "PlanRefused",
    "check_compatibility",
    "core_platform",
    "load_addons",
]

PROTOCOL_VERSION = "1.0"
ERROR_CODES = frozenset({
    "invalid_request", "unsupported_version", "unsupported_operation", "refused", "not_found", "backend_error",
    "internal",
})
KINDS = ("scheduler", "container_runtime", "data_handler", "store", "remote")
_EXIT = {"invalid_request": 2, "unsupported_version": 3, "internal": 4}


class AddonError(Exception):
    """An add-on call failed (section 1.4).

    Attributes
    ----------
    code : str
        One of :data:`ERROR_CODES`, or ``invalid_config`` for a bad ``[addons]`` entry.
    message : str
    details : dict
    """

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details = code, message, dict(details or {})


class PlanRefused(ValueError):
    """The plan is refused at plan time (section 5); ``code`` is a refusal code such as ``incompatible.pairing``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Addon:
    """One configured add-on.

    Parameters
    ----------
    name, kind, version : str
        From ``[addons.<name>]``.
    command : list of str
        Program and fixed arguments; the program is run without a shell.
    settings : dict
        The ``settings`` table; copied into each request, never recorded.
    """

    name: str
    kind: str
    version: str
    command: list[str]
    settings: dict[str, Any] = field(default_factory=dict)
    stderr_text: str = ""

    def call(self, op: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return the ``result`` object (section 1.2 to 1.5).

        Raises
        ------
        AddonError
            With the add-on's error code, or ``internal`` when the exit code, the output or the envelope disagree.
        """
        req = {"protocol_version": PROTOCOL_VERSION, "kind": self.kind, "op": op,
               "settings": self.settings, "params": dict(params or {})}
        try:
            p = subprocess.run(self.command, input=json.dumps(req), capture_output=True, text=True,
                               check=False)
        except OSError as exc:
            raise AddonError("internal", f"cannot start {self.command[0]!r}: {exc.strerror or exc}") from exc
        self.stderr_text += p.stderr
        try:
            doc = json.loads(p.stdout)
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or "ok" not in doc:
            raise AddonError("internal", f"unparseable or missing response (exit code {p.returncode})")
        if str(doc.get("protocol_version", "")).split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
            raise AddonError("unsupported_version", f"response protocol_version {doc.get('protocol_version')!r}")
        if doc["ok"] is True and p.returncode == 0 and isinstance(doc.get("result"), dict):
            return doc["result"]
        err = doc.get("error")
        if doc["ok"] is False and p.returncode != 0 and isinstance(err, dict):
            code = err.get("code")
            if code in ERROR_CODES:
                raise AddonError(code, str(err.get("message", "")), err.get("details"))
        raise AddonError("internal", f"exit code {p.returncode} disagrees with the response")

    def capabilities(self) -> dict[str, Any]:
        """Call ``capabilities`` and check that kind and version equal the configured ones (section 1.7)."""
        try:
            caps = self.call("capabilities")
        except AddonError as exc:
            if exc.code == "unsupported_version":
                raise PlanRefused("incompatible.protocol", _refusal("incompatible.protocol", self,
                                  "does not speak protocol 1.x", "plan", "protocol major number 1")) from exc
            raise
        if caps.get("kind") != self.kind:
            raise PlanRefused("incompatible.kind", _refusal("incompatible.kind", self,
                              f"reports kind {caps.get('kind')!r}", "plan", f"kind {self.kind!r}"))
        if caps.get("version") != self.version:
            raise PlanRefused("incompatible.version", _refusal("incompatible.version", self,
                              f"reports version {caps.get('version')!r}", "plan", f"version {self.version!r}"))
        return caps

    def flush_stderr(self, log_dir: str) -> None:
        """Append the collected standard error text to ``<log_dir>/addon-<name>.err`` (section 1.2)."""
        if self.stderr_text:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, f"addon-{self.name}.err"), "a", encoding="utf-8") as fh:
                fh.write(self.stderr_text)
            self.stderr_text = ""


def load_addons(root: str | os.PathLike) -> dict[str, Addon]:
    """Read ``[addons.<name>]`` of ``<root>/.stradler/config.toml`` (section 1.7); ``{}`` without the file."""
    path = os.path.join(os.fspath(root), CONFIG_DIR, "config.toml")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    out: dict[str, Addon] = {}
    for name, a in (cfg.get("addons") or {}).items():
        if not isinstance(a, dict) or a.get("kind") not in KINDS or not isinstance(a.get("version"), str) \
                or not isinstance(a.get("command"), str) or not a["command"]:
            raise AddonError("invalid_config", f"[addons.{name}] needs kind, version and command")
        if any(c.isspace() for c in a["command"]):
            raise AddonError("invalid_config", f"[addons.{name}] command must be one executable with no "
                             f"arguments (ADDONS 1.7); got {a['command']!r}")
        out[name] = Addon(name, a["kind"], a["version"], [a["command"]], dict(a.get("settings") or {}))
    return out


def core_platform() -> str:
    """Platform id of the core, ``<system>-<machine>`` in lower case (section 5)."""
    return f"{_platform.system().lower()}-{_platform.machine().lower()}"


def _refusal(code: str, a: Addon, missing: str, what_name: str, needs: str) -> str:
    return f'stradler: plan refused: {code}: {a.kind} "{a.name}" {a.version} {missing}; {what_name}: {needs}.'


def _refuse(code: str, a: Addon, missing: str, what_name: str, needs: str) -> None:
    raise PlanRefused(code, _refusal(code, a, missing, what_name, needs))


def check_compatibility(
    plan: Any,
    recipe: Any,
    *,
    scheduler: Addon | None,
    runtimes: Mapping[str, Addon],
    root: str | os.PathLike,
    names: Sequence[str] = (),
    binds: Sequence[Mapping[str, str]] = (),
    runtime_of: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """Check scheduler and runtime against the plan (section 5); it runs and submits nothing.

    Parameters
    ----------
    plan : Plan
    recipe : Recipe
    scheduler : Addon or None
        The scheduler add-on; ``None`` for the built-in local scheduler (no check of its side).
    runtimes : mapping
        Runtime add-ons by name.
    root : path
        Project root; image paths are relative to it.
    names : sequence of str
        Job names the run will submit.
    binds : sequence of dict
        Binds the run will pass to ``wrap``.
    runtime_of : mapping, optional
        Container name to runtime add-on name; default: the only runtime.
    platform : str, optional
        Core platform id; default :func:`core_platform`.

    Returns
    -------
    dict
        ``{"scheduler": caps or None, "runtimes": {name: caps}, "images": {container: inspect result}}``.

    Raises
    ------
    PlanRefused
        With the first refused rule.
    """
    plat = platform or core_platform()
    root = os.fspath(root)
    out: dict[str, Any] = {"scheduler": None, "runtimes": {}, "images": {}, "runtime_of": {}}
    planned = [t for t in plan.tasks() if t.status == "planned"]
    used = sorted({recipe.step(t.step).container for t in planned} - {"none"})
    sched_caps: dict[str, Any] | None = None
    if scheduler is not None:
        sched_caps = out["scheduler"] = scheduler.capabilities()
        if plat not in sched_caps.get("platforms", []):
            _refuse("incompatible.platform", scheduler, f"does not run on platform {plat!r}", "plan", f"platform {plat}")
        deps = sched_caps.get("dependency_kinds", [])
        if "end" not in deps:
            seg = 2 if len([s for s in plan.segments if any(c.tasks for c in s.chains)]) > 1 else "finalize job"
            _refuse("incompatible.dependency", scheduler, 'does not support dependency kind "end"',
                    f"segment {seg}", 'dependency kind "end"')
        for s in plan.segments:
            if len(s.chains) > 1 and not sched_caps.get("array_jobs"):
                _refuse("incompatible.array", scheduler, "does not support array jobs", f'step "{s.steps[0]}"',
                        f"an array of {len(s.chains)} tasks")
            for sn in s.steps:
                for k in recipe.step(sn).resources:
                    if k not in sched_caps.get("resources", []):
                        _refuse("incompatible.resource", scheduler, f'cannot request resource "{k}"', f'step "{sn}"',
                                f'resource "{k}"')
        longest = sched_caps.get("job_name_max")
        for n in names:
            if isinstance(longest, int) and len(n) > longest:
                _refuse("incompatible.name", scheduler, f"limits job names to {longest} characters",
                        f'job "{n}"', f"{len(n)} characters")
    for cname in used:
        if not runtimes:
            raise PlanRefused("incompatible.missing_add_on",
                              f'stradler: plan refused: incompatible.missing_add_on: no container_runtime add-on '
                              f'is configured; container "{cname}": needs one.')
        rname = (runtime_of or {}).get(cname) or min(runtimes)
        rt = runtimes[rname]
        caps = out["runtimes"].get(rname) or rt.capabilities()
        out["runtimes"][rname] = caps
        out["runtime_of"][cname] = rname
        if plat not in caps.get("platforms", []):
            _refuse("incompatible.platform", rt, f"does not run on platform {plat!r}", f'container "{cname}"',
                    f"platform {plat}")
        if "sha256" not in caps.get("digest_algorithms", []):
            _refuse("incompatible.digest", rt, "does not support digest algorithm sha256", f'container "{cname}"',
                    "sha256")
        for b in binds:
            if b["mode"] not in caps.get("bind_modes", []):
                _refuse("incompatible.bind_mode", rt, f'does not support bind mode "{b["mode"]}"',
                        f'container "{cname}"', f'bind mode "{b["mode"]}"')
        c = recipe.containers[cname]
        rel = c.pin.path or c.image
        if not rel:
            raise PlanRefused("incompatible.image_format",
                              f'stradler: plan refused: incompatible.image_format: container "{cname}" has no image path.')
        info = rt.call("inspect", {"image": os.path.join(root, rel)})
        out["images"][cname] = info
        if info.get("format") not in caps.get("image_formats", []):
            _refuse("incompatible.image_format", rt, f'does not run image format "{info.get("format")}"',
                    f'container "{cname}"', f'image format "{info.get("format")}"')
        if sched_caps is not None and info.get("format") not in sched_caps.get("container_image_formats", []):
            assert scheduler is not None
            _refuse("incompatible.pairing", scheduler, f'does not run image format "{info.get("format")}" '
                    f'of container_runtime "{rt.name}" {rt.version}', f'container "{cname}"',
                    f'image format "{info.get("format")}"')
    return out
