"""Command line: ``stradler init``, ``validate`` and ``describe``."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .describe import agent_view, describe_project, render_agent_text, render_text
from .init import InitError, init_project
from .project import ProjectError, load_project
from .validate import validate_path, validate_warnings

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``stradler`` command."""
    p = argparse.ArgumentParser(prog="stradler", description="Strata handler.")
    p.add_argument("--version", action="version", version=f"stradler {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="create stradler.json and .stradler/ from a project template")
    i.add_argument("--template", required=True, help="template file (.yaml, .yml, .toml or .json)")
    i.add_argument("--project-id", help="project identifier (default: template or directory name)")
    i.add_argument("--json", action="store_true", help="machine-readable output")
    i.add_argument("path", nargs="?", default=".", help="directory to initialise (default: .)")
    ad = sub.add_parser("adopt", help="take an existing directory in as a project, or copy files in from a plan")
    ad.add_argument("--template", help="template file for areas and config (in place mode)")
    ad.add_argument("--project-id", help="project identifier (default: directory name)")
    ad.add_argument("--dry-run", action="store_true", help="print the proposal and change nothing")
    ad.add_argument("--plan", help="copy mode: JSON list of {source, dest, size, digest}; path is the project")
    ad.add_argument("--no-keep-times", action="store_true", help="copy mode: do not set the dest times to the source's")
    ad.add_argument("--bwlimit", help="copy mode: rate limit in bytes/s, with optional K, M or G suffix")
    ad.add_argument("--progress-interval", type=float, default=10.0, help="copy mode: seconds between progress lines (default: 10)")
    ad.add_argument("--json", action="store_true", help="machine-readable output")
    ad.add_argument("path", nargs="?", default=".", help="directory to adopt, or the project in copy mode (default: .)")
    v = sub.add_parser("validate", help="validate the project at or above a directory")
    v.add_argument("--json", action="store_true", help="machine-readable output")
    v.add_argument("path", nargs="?", default=".", help="directory to start from (default: .)")
    s = sub.add_parser("seal", help="seal a dataset of a sealed area: read-only, snapshot document, verify")
    s.add_argument("dataset", nargs="?", help="dataset folder")
    s.add_argument("--layout", choices=("flat", "tree"), default="flat", help="snapshot layout (default: flat)")
    s.add_argument("--stream", action="store_true", help="store the entries as a stream (layout flat)")
    s.add_argument("--grade", help="grade to store in the document (not interpreted)")
    s.add_argument("--provenance-file", help="JSON file with the provenance object to store")
    s.add_argument("--rollback", action="store_true", help="undo an interrupted seal of the dataset")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    v2 = sub.add_parser(
        "verify",
        help="check a dataset or a snapshot digest against its snapshot (read-only)",
        description="Exit status: 0 match, 1 differences (missing, added, changed, unreadable), "
        "2 unreachable store, 3 error (including a usage error).",
        epilog="Exit status: 0 match, 1 differences, 2 unreachable, 3 error.",
    )
    v2.add_argument("target", nargs="?", help="dataset folder or sha256: snapshot digest")
    v2.add_argument("--replicas", metavar="AREA", help="check the area's latest snapshots against its declared "
                    "replica stores (exit: 1 differs, 2 unreachable, 3 verify not supported or error)")
    v2.add_argument("--store", help="check the copy at [stores.NAME] path of .stradler/local.toml")
    v2.add_argument("--subtree", help="check one directory of a tree-layout snapshot")
    v2.add_argument("--json", action="store_true", help="machine-readable output")
    df = sub.add_parser(
        "diff",
        help="compare two snapshots of a dataset (reads documents only)",
        description="Exit status: 0 no difference, 1 differences, 3 error.",
    )
    df.add_argument("dataset", help="dataset folder (project-relative or a path inside the project)")
    df.add_argument("paths", nargs="*", help="limit to these paths or directories")
    df.add_argument("--from", dest="from_digest", metavar="sha256:A", help="from snapshot (default: previous of --to)")
    df.add_argument("--to", dest="to_digest", metavar="sha256:B|working",
                    help="to snapshot (default: head) or 'working' for the dataset folder as it is")
    df.add_argument("--depth", choices=("size", "sha256"), default="sha256",
                    help="hashing depth for --to working (default sha256)")
    df.add_argument("--include", action="append", default=[], metavar="GLOB", help="keep paths matching GLOB")
    df.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="drop paths matching GLOB")
    df.add_argument("--stat", action="store_true", help="header and summary only")
    df.add_argument("--json", action="store_true", help="machine-readable output (diff_version 1)")
    d = sub.add_parser("describe", help="describe the project's areas, roles, stores and add-ons")
    d.add_argument("--json", action="store_true", help="versioned JSON document")
    d.add_argument("--agent", action="store_true", help="only what agents need: area, role, agent keys, grade")
    d.add_argument("path", nargs="?", default=".", help="directory to start from (default: .)")
    r = sub.add_parser("retention", help="retention policy records of a dataset or area (no evaluation, no guards)",
                       description="Exit status: 0, 3 on an error (unknown dataset or area, two policy heads, "
                       "pin outside the lineage, invalid document).")
    rs = r.add_subparsers(dest="rcmd", required=True)

    rsh = rs.add_parser("show", help="head policy, revision lineage and effective rules")
    rsh.add_argument("dataset", nargs="?", help="dataset folder")
    rsh.add_argument("--area", help="show the policy of an area instead")
    rsh.add_argument("--json", action="store_true", help="machine-readable output")
    rse = rs.add_parser("set", help="write a new revision of a policy")
    rse.add_argument("dataset", nargs="?", help="dataset folder")
    rse.add_argument("--area", help="policy of an area instead of a dataset")
    g = rse.add_mutually_exclusive_group()
    g.add_argument("--keep-last", type=int, metavar="N", help="keep the newest N versions (at least 1)")
    g.add_argument("--no-keep-last", action="store_true", help="remove the count rule")
    g = rse.add_mutually_exclusive_group()
    g.add_argument("--keep-days", type=int, metavar="D", help="keep a version D days after it was superseded")
    g.add_argument("--no-keep-days", action="store_true", help="remove the time rule")
    rse.add_argument("--hold-copies", type=int, metavar="K", help="held locations each kept version needs")
    rse.add_argument("--merge", action="store_true", help="resolve two heads by their union")
    rse.add_argument("--json", action="store_true", help="machine-readable output")
    rpi = rs.add_parser("pin", help="pin a version by snapshot digest, or every input of a run")
    rpi.add_argument("dataset", nargs="?", help="dataset folder")
    rpi.add_argument("snapshot", nargs="?", help="sha256:<hex> snapshot digest")
    rpi.add_argument("--reason", help="why (at most 200 characters, no paths)")
    rpi.add_argument("--from-run", metavar="RUN", help="run folder or run.json: pin its input versions")
    rpi.add_argument("--json", action="store_true", help="machine-readable output")
    run_ = rs.add_parser("unpin", help="remove a pin")
    run_.add_argument("dataset", help="dataset folder")
    run_.add_argument("snapshot", help="sha256:<hex> snapshot digest")
    run_.add_argument("--json", action="store_true", help="machine-readable output")
    w = sub.add_parser("workflow", help="job-side entry points of a run on a scheduler add-on")
    ws = w.add_subparsers(dest="wcmd", required=True)
    c = ws.add_parser("chain", help="run one chain of a segment (job body; index from STRADLER_TASK_INDEX)")
    c.add_argument("run_dir")
    c.add_argument("segment", type=int)
    f = ws.add_parser("finalize", help="write the final status of a run to run.json (finalize job)")
    f.add_argument("run_dir")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit status: 0 on success, 1 for a refusal or an invalid project, 2 for usage errors.
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        if e.code == 2 and (sys.argv[1:] if argv is None else argv)[:1] in (["verify"], ["diff"]):
            return 3  # verify and diff report every usage error as 3
        raise
    if args.cmd in ("seal", "verify"):
        return _seal_verify(args)
    if args.cmd == "diff":
        return _diff(args)
    if args.cmd == "retention":
        return _retention(args)
    if args.cmd == "workflow":
        from .workflow import scheduled
        if args.wcmd == "chain":
            return scheduled.run_chain(args.run_dir, args.segment)
        try:
            scheduled.finalize_run(args.run_dir)
        except (OSError, ValueError, KeyError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "describe":
        try:
            prj = load_project(args.path)
        except ProjectError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if prj.read_errors:
            for m in prj.read_errors:
                print(f"error: {m}", file=sys.stderr)
            return 1
        doc = describe_project(prj)
        if args.agent:
            doc = agent_view(doc)
        if args.json:
            print(json.dumps(doc, indent=2))
        else:
            print(render_agent_text(doc) if args.agent else render_text(doc))
        return 0
    try:
        if args.cmd == "adopt":
            from . import adopt
            if args.plan and args.dry_run:
                rep = adopt.dry_run_plan(args.plan, args.path)
                if args.json:
                    print(json.dumps(rep))
                else:
                    print("dry run: " + ("a real run would start" if rep["ok"] else "a real run would be refused"))
                    for k in ("to_copy", "already_match", "dest_conflict", "source_missing", "source_changed",
                              "dest_outside_areas"):
                        print(f"  {k}: {rep[k]['files']} file(s), {rep[k]['bytes']} bytes")
                    print(f"  free {rep['free_bytes']} bytes, to copy {rep['bytes_to_copy']} bytes")
                return 0 if rep["ok"] else 1
            if args.plan:
                prog = (lambda d: print(json.dumps(d), file=sys.stderr, flush=True)) if args.json else None
                summary = adopt.adopt_plan(
                    args.plan, args.path, keep_times=not args.no_keep_times,
                    bwlimit=adopt.parse_rate(args.bwlimit) if args.bwlimit else None,
                    progress=prog, progress_interval=args.progress_interval)
                intr = summary["interrupted"]
                print(json.dumps({"ok": not intr, **summary}) if args.json else
                      f"{'interrupted: ' if intr else ''}copied {summary['copied']}, skipped {summary['skipped']} "
                      f"({summary['bytes_copied']} bytes)")
                if intr:
                    return 130 if summary["signal"] == "SIGINT" else 143
            elif args.dry_run:
                prop = adopt.propose(args.path, args.template, args.project_id)
                print(json.dumps({"ok": True, "dry_run": True, **prop}, indent=None if args.json else 2))
            else:
                root = adopt.adopt_in_place(args.path, args.template, args.project_id)
                print(json.dumps({"ok": True, "root": str(root)}) if args.json else f"adopted {root}")
            return 0
        if args.cmd == "init":
            root = init_project(args.template, args.path, args.project_id)
            print(json.dumps({"ok": True, "root": str(root)}) if args.json else f"initialised {root}")
            return 0
        root, problems = validate_path(args.path)
        warnings = validate_warnings(load_project(root)) if not problems else []
    except (InitError, ProjectError) as e:
        print(json.dumps({"ok": False, "error": str(e)}) if args.json else f"error: {e}", file=sys.stderr if not args.json else sys.stdout)
        return 1
    if args.json:
        print(json.dumps({"ok": not problems, "root": str(root), "problems": problems, "warnings": warnings}))
    elif problems:
        print(f"{root}: {len(problems)} problem(s)", file=sys.stderr)
        for m in problems:
            print(f"  {m}", file=sys.stderr)
    else:
        print(f"{root}: valid")
    for m in warnings:
        print(f"warning: {m}", file=sys.stderr)
    return 1 if problems else 0


def _retention(args: argparse.Namespace) -> int:
    from .project import load_project
    from .retention import (
        RetentionError,
        pin_from_run,
        pin_version,
        set_policy,
        show,
        unpin_version,
    )

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"error: {msg}", file=sys.stderr)
        return 3

    try:
        area = getattr(args, "area", None)
        start = "." if args.dataset is None or area else os.path.realpath(args.dataset)
        prj = load_project(start)
        if prj.read_errors:
            return fail("; ".join(prj.read_errors))
        root = str(prj.root)
        rel = os.path.relpath(os.path.realpath(args.dataset), root) if args.dataset is not None else None
        if args.rcmd == "show":
            out = show(root, prj.structure, dataset=rel, area=area)
            if args.json:
                print(json.dumps({"ok": True, **out}))
            else:
                sc = out["scope"]
                print(f"{sc['kind']} {sc.get('dataset', sc.get('area'))}")
                h = out["head"]
                if h is None:
                    print("  no policy")
                else:
                    print(f"  head {h['digest']}  keep_last {h['keep_last']}  keep_days {h['keep_days']}  "
                          f"hold_copies {h.get('hold_copies', 1)}  pins {len(h['pins'])}")
                for r in out["revisions"]:
                    print(f"  revision {r['digest']}  {r['created']}")
                if "effective" in out:
                    e = out["effective"]
                    print(f"  effective: keep_last {e['keep_last']}  keep_days {e['keep_days']}  "
                          f"hold_copies {e['hold_copies']}  pins {len(e['pins'])}")
            return 0
        if args.rcmd == "set":
            kw: dict = {}
            if args.keep_last is not None:
                kw["keep_last"] = args.keep_last
            if args.no_keep_last:
                kw["keep_last"] = None
            if args.keep_days is not None:
                kw["keep_days"] = args.keep_days
            if args.no_keep_days:
                kw["keep_days"] = None
            if args.hold_copies is not None:
                kw["hold_copies"] = args.hold_copies
            res = set_policy(root, prj.structure, dataset=rel, area=area, merge=args.merge, **kw)
        elif args.rcmd == "pin":
            if args.from_run:
                if args.dataset or args.snapshot:
                    return fail("--from-run takes no dataset or snapshot")
                res = pin_from_run(root, prj.structure, args.from_run, reason=args.reason)
                if args.json:
                    print(json.dumps({"ok": True, **res}))
                else:
                    for p in res["pins"]:
                        print(f"{p['dataset']}: {'pinned' if p['written'] else 'already pinned'} {p['snapshot']}")
                return 0
            if rel is None or not args.snapshot:
                return fail("pin needs a dataset and a snapshot digest, or --from-run")
            res = pin_version(root, prj.structure, rel, args.snapshot, args.reason or "")
        else:
            res = unpin_version(root, prj.structure, rel, args.snapshot)
    except (ProjectError, RetentionError, OSError, ValueError) as e:
        return fail(str(e))
    if args.json:
        print(json.dumps({"ok": True, **res}))
    else:
        print(f"{'wrote' if res['written'] else 'unchanged'} {res['document']['digest']}")
    return 0


def _seal_verify(args: argparse.Namespace) -> int:
    from .project import load_project
    from .seal import SealError, rollback_seal, seal_dataset
    from .verify import VerifyError, exit_code, verify_dataset

    bad = 1 if args.cmd == "seal" else 3

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"error: {msg}", file=sys.stderr)
        return bad

    if args.cmd == "verify" and args.replicas:
        return _verify_replicas(args)
    target = args.target if args.cmd == "verify" else args.dataset
    digest = args.cmd == "verify" and target is not None and target.startswith("sha256:")
    try:
        if target is None:
            return fail("seal needs a dataset" if args.cmd == "seal" else "verify needs a target or --replicas")
        abspath = "" if digest else os.path.realpath(target)
        prj = load_project("." if digest else abspath)
        root = str(prj.root)
        rel = target if digest else os.path.relpath(abspath, root)
        if prj.read_errors:
            return fail("; ".join(prj.read_errors))
        if args.cmd == "seal":
            if args.rollback:
                out = rollback_seal(root, prj.structure, rel)
                print(json.dumps({"ok": True, **out}) if args.json else f"rolled back {rel}: {out['restored']} modes restored")
                return 0
            prov = None
            if args.provenance_file:
                with open(args.provenance_file, encoding="utf-8") as fh:
                    prov = json.load(fh)
            out = seal_dataset(root, prj.structure, rel, local=prj.local, layout=args.layout, stream=args.stream,
                               grade=args.grade, provenance=prov)
        else:
            out = verify_dataset(root, prj.structure, rel, store=args.store, local=prj.local, subtree=args.subtree)
    except (ProjectError, SealError, VerifyError, OSError, ValueError) as e:
        return fail(str(e))
    code = exit_code(out["result"])
    if args.json:
        print(json.dumps({"ok": code == 0, **out}))
    else:
        c = out.get("counts")
        line = f"{out['dataset']}: {out['result']}"
        if c:
            line += " (" + ", ".join(f"{k} {v}" for k, v in c.items()) + ")"
        print(("sealed; " if args.cmd == "seal" else "") + line)
        for cat, paths in out.get("lists", {}).items():
            for pth in paths:
                print(f"  {cat}: {pth}")
    return code


def _verify_replicas(args: argparse.Namespace) -> int:
    from .project import load_project
    from .replicas import ReplicaError, exit_code, verify_replicas

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"error: {msg}", file=sys.stderr)
        return 3

    if args.target or args.store or args.subtree:
        return fail("--replicas takes no target, --store or --subtree")
    try:
        prj = load_project(".")
        if prj.read_errors:
            return fail("; ".join(prj.read_errors))
        out = verify_replicas(prj, args.replicas)
    except (ProjectError, ReplicaError, OSError, ValueError) as e:
        return fail(str(e))
    code = exit_code(out)
    if args.json:
        print(json.dumps({"ok": code == 0, **out}))
    else:
        for c in out["checks"]:
            line = f"{out['area']} -> {c['store']}: {c['dataset']}: {c['result']}"
            if c.get("counts"):
                line += " (" + ", ".join(f"{k} {v}" for k, v in c["counts"].items()) + ")"
            print(line + (f": {c['message']}" if c.get("message") else ""))
        for m in out["problems"]:
            print(f"  problem: {m}")
    return code


def _diff(args: argparse.Namespace) -> int:
    from .diff import DiffError, diff_dataset, exit_code, render_text

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"error: {msg}", file=sys.stderr)
        return 3

    try:
        abspath = os.path.realpath(args.dataset)
        inside = os.path.isdir(abspath)
        prj = load_project(abspath if inside else ".")
        if prj.read_errors:
            return fail("; ".join(prj.read_errors))
        root = str(prj.root)
        rel = os.path.relpath(abspath, root) if inside else args.dataset
        out = diff_dataset(root, prj.structure, rel, from_digest=args.from_digest, to_digest=args.to_digest,
                           paths=args.paths, include=args.include, exclude=args.exclude, depth=args.depth)
    except (ProjectError, DiffError, OSError, ValueError) as e:
        return fail(str(e))
    print(json.dumps(out) if args.json else render_text(out, stat=args.stat))
    return exit_code(out)
