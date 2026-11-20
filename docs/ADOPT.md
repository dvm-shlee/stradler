# stradler adopt

`stradler adopt` takes an existing directory in as a project.

## In place

`stradler adopt <dir> [--template T] [--project-id ID] [--dry-run] [--json]`

Proposes one area per top-level folder (hidden folders skipped): role `input`, mutability `immutable`, grade
`private` (schema `structure@1.1.0`). With `--template`, the template's areas and config are used as they are and only
folders no template area covers get the default area. `--dry-run` prints the proposal and changes nothing. Without
it, only `stradler.json` and `.stradler/` are written: no existing file is modified and no folder is created. A
directory that already is a project is refused, and so is one with no top-level folder (unless a template gives areas).

## Copy mode

`stradler adopt --plan <file> [<project>]`

The plan is a JSON list of `{source, dest, size, digest}`; `digest` is `sha256:<hex>` (SNAPSHOTS.md, section 4.4),
`dest` a relative path inside the project (not under `.stradler/`). Every dest is checked first: one that exists with
other content refuses the whole plan before anything is copied; one that already matches is skipped. Each file is
copied to `<dest>.stradler-part`, verified for size and sha256, then renamed. A mismatch removes the temporary file
and stops with an error. Sources are only read. A rerun after an interruption finishes the rest with the same result.
A summary record (counts, bytes, UTC times, no paths) is written to `.stradler/adopt/summary-<time>.json` and printed.

## Provenance

`write_snapshot(..., provenance={origin, checked_at, metadata})` (SNAPSHOTS.md, section 3.5) copies the object into
the version 2 snapshot document. `origin` is a string, `checked_at` a UTC timestamp, `metadata` a canonical JSON
object. Stradler never interprets it and it is not part of any digest.
