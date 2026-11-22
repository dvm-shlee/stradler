# stradler adopt

`stradler adopt` takes an existing directory in as a project, or copies files into a project from a plan.

## Synopsis and exit codes

- In place: `stradler adopt <dir> [--template T] [--project-id ID] [--dry-run] [--json]`
- Copy mode: `stradler adopt --plan <file> [<project>] [--dry-run] [--json] [--no-keep-times] [--no-record-verified] [--bwlimit RATE] [--progress-interval S]`

`--plan` selects copy mode; with `--dry-run` it is the copy-mode dry run. `--template` and `--project-id` apply to in
place mode only. `<project>` (default `.`) is the project in copy mode and the directory to adopt in place mode.

Exit status (both modes): 0 success; 1 a refusal or an error (see below); 2 a usage error (argument parsing);
130 a copy run interrupted by SIGINT; 143 a copy run interrupted by SIGTERM. With `--json` an error is printed on
stdout as `{"ok": false, "error": "..."}`; without it, on stderr as `error: ...`.

## In place

Proposes one area per top-level folder (hidden folders skipped): role `input`, mutability `immutable`, grade
`private` (schema `structure@1.1.0`). With `--template`, the template's areas and config are used as they are and only
folders no template area covers get the default area. `--dry-run` prints the proposal and changes nothing. Without
it, only `stradler.json` and `.stradler/` are written: no existing file is modified and no folder is created. A
directory that already is a project is refused, and so is one with no top-level folder (unless a template gives areas).

## Copy mode

The plan is a JSON list of `{source, dest, size, digest}`; `digest` is `sha256:<hex>` (SNAPSHOTS.md, section 4.4),
`dest` a relative path inside the project (not under `.stradler/`, no empty, `.` or `..` segments). A malformed plan
is refused before any check of the project.

Pre-checks of a real run, in this order. Any failure refuses the whole plan before anything is copied:

1. Every dest must lie under a declared area, by the same rule `validate` uses. Otherwise the run stops with
   `N dest(s) under no declared area, first: <dest>` (exit 1). A dest outside every area is never copied.
2. A dest that leaves the project, or is a symbolic link, is refused.
3. A dest that exists with other content than the plan states (size and sha256 are checked) is refused.
4. A dest that already matches is skipped. A rerun after an interruption therefore finishes the rest with the same
   result.
5. For an entry that must be copied, a source that is not a regular file is refused.

Each remaining file is copied to `<dest>.stradler-part`, verified for size and sha256, given the source's access and
modification times, then renamed to its dest. A mismatch removes the temporary file and stops with an error (exit 1).
Sources are only read. Permissions and owner are not copied.

### Keep times

By default the dest's atime and mtime are set to the source's (nanosecond precision) after verify and before the
rename. `--no-keep-times` leaves the dest with the time of the copy.

### Rate limit

`--bwlimit RATE` limits the copy to RATE bytes per second. RATE is a positive number with an optional `K`, `M` or `G`
suffix (powers of 1024, case-insensitive, for example `512K` or `1.5M`); anything else is refused (exit 1). The
limit paces each file separately: the clock restarts for every file, so the limit is a per-file rate, not a cap on
the total of the run. The sha256 pass that verifies each copy is not paced. Skipped files take no budget.

### Progress

With `--json`, progress goes to stderr as JSON Lines, one object per line:
`{"files_done": N, "files_total": N, "bytes_done": N, "bytes_total": N}`.
`files_done` counts the skipped and the copied files; `bytes_done` adds the bytes of the file in progress. A line is
written when at least `--progress-interval` seconds (float, default 10) have passed since the previous line, and one
final line is written at the end of the run, including an interrupted run. Without `--json` no progress is printed.

### Interruption

SIGINT and SIGTERM are caught during the run. The current file stops at its next chunk and its temporary
`<dest>.stradler-part` is removed, so no partial dest is left; no further file is started. A signal that arrives while
a file is being verified or renamed lets that file finish. The summary is written with `interrupted: true` and
`signal` set to `SIGINT` or `SIGTERM`, and the run exits 130 (SIGINT) or 143 (SIGTERM). With `--json` the printed
summary has `"ok": false`; in text the line starts with `interrupted: `. The previous signal handlers are restored
when the run ends. Rerun the same command to resume: files already copied are skipped.

### Summary record

After a run (interrupted or not) the summary is written to `.stradler/adopt/summary-<UTC time>-<pid>.json` (time as
`YYYYMMDDTHHMMSSZ`) and printed. Its fields are `files` (plan entries), `copied`, `skipped` (dests that already
matched), `bytes_total` (sum of the plan's sizes), `bytes_copied`, `interrupted`, `started` and `finished` (UTC,
`YYYY-MM-DDTHH:MM:SSZ`), and `signal` when interrupted. It holds counts, bytes and times, no paths. Printed with
`--json`, the record gets an added `ok` key (`true`, or `false` when interrupted); in text it is one line,
`copied N, skipped M (B bytes)`, prefixed with `interrupted: ` when interrupted.

## Dry run (copy mode)

`stradler adopt --plan <file> --dry-run [--json] [<project>]` runs every pre-check above, copies nothing, writes no
summary record, and lists all problems instead of stopping at the first. Each plan entry is counted in each class
that applies, so a class count can overlap another one (`dest_outside_areas` with any other class), and the class
counts can add up to more than `files`.

| Class | Meaning |
|---|---|
| `to_copy` | no problem found; a real run would copy the file |
| `already_match` | the dest exists and matches the plan (size and sha256 of the dest file are read) |
| `dest_conflict` | the dest leaves the project, is a symbolic link, or exists with other content |
| `source_missing` | the source is not a regular file |
| `source_changed` | the source size differs from the plan (only the size is compared, the source is not hashed) |
| `dest_outside_areas` | the dest lies under no declared area |

With `--json` the report is one line with the fields `ok`, `dry_run` (true), `files` (plan entries), one
`{files, bytes}` object per class, `free_bytes` (free space on the project's file system), `bytes_to_copy` (bytes of
the `to_copy` class) and `enough_space` (`free_bytes` covers `bytes_to_copy`). Text output prints
`dry run: a real run would start` or `dry run: a real run would be refused`, one line per class, then the free space
and the bytes to copy. `ok` is true only when a real run would start: no class other than `to_copy` and
`already_match` is non-empty and `enough_space` is true. Exit 0 when `ok`, otherwise 1. The dry run does not check
`--bwlimit`.

## Validate

`stradler validate` applies the same area rule as copy mode. A file under no declared area is a warning, not a
problem: `validate` stays ok, and it reports one warning per top-level entry that holds such files (hidden top-level
entries and the project marker are skipped). Warnings are computed only when the project has no problems. In text they
are printed on stderr as `warning: <top>: N file(s) under no declared area` after the `valid` line; with `--json` they
are the `warnings` list of the output (`ok`, `root`, `problems`, `warnings`).

## Provenance

`write_snapshot(..., provenance={origin, checked_at, metadata})` (SNAPSHOTS.md, section 3.5) copies the object into
the version 2 snapshot document. `origin` is a string, `checked_at` a UTC timestamp, `metadata` a canonical JSON
object. Stradler never interprets it and it is not part of any digest.

## Verified digests (machine-local cache)

After each verified copy, and for each file that already matched (the pre-check read its size and sha256 to find it
matching), copy mode appends one JSON line to `<project>/.stradler/local/verified.jsonl`. The line is written after
the dest's times are set and the temporary file is renamed to the dest.

| Member | Type | Meaning |
|---|---|---|
| `verified_version` | integer, `1` | Version of the line format. Other values are malformed. |
| `dest` | string | Project-relative POSIX path of the file (the plan's `dest`). |
| `size` | integer | Size of the dest in bytes. |
| `mtime_ns` | integer | Modification time of the final dest in nanoseconds, read after the rename. |
| `sha256` | string | `sha256:<hex>` of the dest, as verified against the plan. |
| `verified_at` | string | UTC time of the record, `YYYY-MM-DDTHH:MM:SSZ`. |

The file is one JSON object per line, compact, appended to on each run (it grows; it is not rewritten). For a path
with several lines the latest one wins. A line is malformed when it is not a JSON object, has another
`verified_version`, or has a member of the wrong type or a `sha256` that is not `sha256:` plus 64 lowercase hex
digits. A malformed line is ignored and counted; it never makes a run fail.

The cache is machine-local and git-ignored: `init` writes `local/` into `.stradler/.gitignore`, and adopt adds the
line when it creates the cache if it is missing. It is not a record and holds no content; it may be deleted at any
time, after which nothing is reused until the next copy run writes it again. `--no-record-verified` turns the
recording off for a copy run.

### Reuse by seal

`stradler seal <dataset> --reuse-verified` takes the digest of a file from the latest cache line for its
project-relative path when the file's size and `mtime_ns` equal the line's; the file is then not read for the
seal's digests. Any other file (no line, or a size or mtime that differs) is read as usual. Without the flag nothing
changes and the cache is not read. The flag is a seal option only; the `snapshot` command does not exist in the
CLI. The Python API `write_snapshot(..., reuse=verified.Reuse(root))` offers the same reuse to code that calls it.

A reused digest is a digest the cache states, not one computed from the bytes in this run. The final verify that
ends every seal reads every file and is not affected by the flag, so a file whose bytes differ from the cache while
its size and mtime still match is reported by that verify.

When the flag is set, the seal's result reports how many digests were reused and how many cache lines were
malformed:

- text: `sealed; <dataset>: <result> (<counts>) [reused N cached digest(s), M cache warning(s)]`; with the flag off the
  bracket is absent;
- `--json`: the result object has the added keys `reused_digests` (N: distinct files whose digest came from the
  cache) and `cache_warnings` (M: malformed cache lines read); both are absent without the flag.
