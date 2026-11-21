# stradler diff: normative specification

Status: normative draft (2026-11-21). Derived from `docs/plans/VERSIONS.md` section 3 and its owner decisions 6, 7
and 8 (recommended options adopted: exit codes as `diff(1)`, exact-digest move detection, empty list as the default
`from` of a first version). Terms are those of `docs/GLOSSARY.md`. Section numbers in parentheses refer to
`docs/SNAPSHOTS.md`, `docs/SCHEMAS.md` and `docs/plans/CONTENT-ON-DEMAND.md` (COD) where stated.

## 1. Purpose

`stradler diff` compares two versions of one dataset and reports which files were added, removed, changed or moved.
It compares snapshot documents, so it needs no content files and no location, except for the working copy, which is
hashed. It never changes a dataset, a store or a record.

## 2. Command

```
stradler diff <dataset> [--from sha256:A] [--to sha256:B|working] [<path>...]
              [--include G] [--exclude G] [--subset NAME] [--depth size|sha256] [--stat] [--json]
```

| Argument or option | Meaning |
|---|---|
| `<dataset>` | Required. The dataset whose versions are compared. |
| `--from sha256:A` | The snapshot on the `from` side. Default: the `previous` of the `to` side. |
| `--to sha256:B` | The snapshot on the `to` side. Default: the head. |
| `--to working` | The dataset folder as it is now (section 6). |
| `<path>...` | Restrict the comparison to these paths (COD 6.1 selection). Resolved on each side separately. |
| `--include G`, `--exclude G` | Glob selection (COD 6.1). Resolved on each side separately. |
| `--subset NAME` | A named subset (COD 6.2). Resolved per snapshot. An unknown name is an error. |
| `--depth size\|sha256` | Hashing depth for the working copy only (section 6). Default `sha256`. Ignored for snapshot against snapshot. |
| `--stat` | Print only the header line and the summary line (section 8). |
| `--json` | Print the document of section 8.2 instead of text. |

Defaults and their interaction:

- With no `--to`, `--to` is the head.
- With no `--from`, `--from` is the `previous` of `--to`. When that `previous` is `null` (the `--to` version is the first
  version of the dataset), the `from` side is the empty entry list (section 7).
- With `--to working` and no `--from`, `--from` is the head.
- `--from` and `--to` each accept a digest only; a version is named by its digest, not by a number.

Validation, all before any output is written:

- `A` and `B` must be version 2 snapshot documents of the same `dataset`. A version 1 document is an error.
- A digest may name any version held in the record area of the dataset, not only the lineage of the head. The lineage
  relation between the two sides is reported (section 8.1). A digest that is not found, or that belongs to another
  dataset, is an error.
- If the dataset has two heads, the default `--to` is an error; the user must name `--to`.
- A selection given as paths, globs or a subset is resolved on each side. A side in which the selection matches nothing
  is not an error; its entries are simply absent from the comparison.

## 3. Entries

An entry is one file of a snapshot (or of the working copy): its path relative to the dataset folder, its size in
bytes and its digest (`sha256:...`). Only regular files are entries. A symbolic link or other non-regular entry in the
working copy is an `unreadable` entry (section 6).

## 4. Categories

Each path of either side is classified once. The categories are:

| Category | Rule |
|---|---|
| `unchanged` | Same path on both sides, same digest. Counted, not listed. |
| `changed` | Same path on both sides, different digest. Listed with size and digest of both sides. |
| `removed` | Path only on the `from` side, not paired as a move. Listed. |
| `added` | Path only on the `to` side, not paired as a move. Listed. |
| `moved` | A removed entry and an added entry with the same digest, paired as in section 4.1. Listed. |
| `absent` | Working copy only (section 6): an entry whose file is not present. Listed. |
| `unreadable` | Working copy only (section 6): a file that cannot be read, a symbolic link or another non-regular entry. Listed with a reason. |

### 4.1 Move pass

Moves are paired after the same-path pass.

1. The removed entries and the added entries are grouped by digest.
2. Within one digest group, removed and added entries are paired one to one in sorted path order (the first removed path
   with the first added path, and so on). Leftovers stay `removed` or `added`.
3. An entry of size 0 is never paired. Empty files stay `removed` or `added`.
4. A pair is reported once, as `moved`, with `from_path` and `to_path`, `size` and `digest`.

Consequences: a path that is `changed` is never a move; a file whose content exists on both sides at unchanged paths
does not make another entry a move (the same-path pass comes first). Pairing uses only the digests in the entries; the
content is not read.

### 4.2 Same content

An `added` entry whose digest equals the digest of an `unchanged` entry or of another entry of the `from` side carries
`same_content_as`: one path of the `from` side (the first in sorted order), or `null` when there is none. The entry stays
`added`. This field is informational and is present in `--json` only.

## 5. Snapshot against snapshot

Both sides are snapshot documents. The comparison reads only the two documents and, as needed, their entry streams
(`flat` layout) or tree nodes (`tree` layout) (SNAPSHOTS 5). No content file is read, so diff works on an `expected` or
`partial` dataset (COD 5.1) and without any location.

- Two `flat` documents: the two sorted entry lists are merged in one pass.
- Two `tree` documents: subtrees whose node digests are equal are skipped without reading below them.
- Mixed layouts: both sides are read as flat lists.
- A missing stream or node file is an error (exit 3).

`--depth` is not used in this mode.

## 6. Head against the working copy

`--to working` lists the regular files of the dataset folder with the rules of SNAPSHOTS 4.3, which are the files sealing
would record. Staging folders of COD 9.3 lie outside the dataset folder and are not listed. The listed files are compared
with the `from` entries:

- `--depth sha256` (default): every present file is read and hashed. Its digest is compared with the entry.
- `--depth size`: a present file whose size equals the entry's size counts as `unchanged`, with no reading. The depth
  is printed with the result (section 8).

Two categories exist only in this mode:

- `absent`: an entry of the `from` side whose file is not present. In an on-demand area (COD 3.1) this is the normal state
  of an entry whose content is not fetched, and it is reported as `absent`. In a `present` area the same entry is
  reported as `removed`.
- `unreadable`: a listed file that cannot be read, a symbolic link, or another non-regular entry. Its reason is given.

Moves (section 4.1) are paired among files that were hashed. Under `--depth size` no working file is hashed, so no move
is reported. A present working file whose size differs from its entry is `changed`, and its `to` digest is `null` in
`--json`; a present file with the entry's size is `unchanged` (section 6, first item).

Diff never writes into the dataset, a store or a record.

## 7. First version

When `--to` is the first version of the dataset (its `previous` is `null`) and no `--from` is given, the `from` side is
the empty entry list. Every entry of the `to` side is `added`, with `same_content_as` `null` (there is nothing to compare
with). The command succeeds and does not fail on the missing `previous`. Text and `--json` output follow sections 8.1 and
8.2 with `from` set to `null`.

An explicit `--from` of another version is an error when that version does not belong to the dataset or is not version 2.

## 8. Output

### 8.1 Text

The first line is the header:

```
diff <dataset> <from-label> -> <to-label>, <relation>
```

- `<from-label>` is `sha256:<digest>`, or `null` (first version).
- `<to-label>` is `sha256:<digest>`, or `working (depth <depth>)`.
- `<relation>` is `ancestor`, `descendant` or `unrelated` when both sides are snapshots; it is omitted when either side is
  `null` or `working`.

Then one line per listed entry, sorted by path (a `moved` entry sorts by `to_path`), with a letter:

| Letter | Line |
|---|---|
| `A` | `A <path>` (added) |
| `D` | `D <path>` (removed) |
| `M` | `M <path> <from size> -> <to size>` (changed) |
| `R` | `R <from_path> -> <to_path>` (moved) |
| `-` | `- <path>` (absent) |
| `!` | `! <path> <reason>` (unreadable) |

The last line is the summary:

```
<n> added, <n> removed, <n> changed, <n> moved, <n> unchanged; +<size>, -<size>
```

- `+<size>` is the total size of `added`; `-<size>` is the total size of `removed`. Sizes are printed in binary units
  (for example `1.2 GiB`). Moves and changes do not contribute to either total.
- If `absent` or `unreadable` counts are not zero, they are appended to the summary as `<n> absent, <n> unreadable`
  before the `;`.
- `--stat` prints only the header line and the summary line.

### 8.2 JSON

`--json` prints one object, with keys in this order:

```
{
  "diff_version": 1,
  "dataset": <string>,
  "from": {"snapshot": <digest>, "snapshot_version": 2} | null,
  "to": {"snapshot": <digest>, "snapshot_version": 2} | {"working": true, "depth": "size" | "sha256"},
  "relation": "ancestor" | "descendant" | "unrelated" | null,
  "selection": <object describing the paths, globs and subset used, or null>,
  "counts": {"added": n, "removed": n, "changed": n, "moved": n, "unchanged": n, "absent": n, "unreadable": n},
  "bytes": {"added": n, "removed": n, "changed_from": n, "changed_to": n},
  "added": [{"path": s, "size": n, "digest": s, "same_content_as": s | null}],
  "removed": [{"path": s, "size": n, "digest": s}],
  "changed": [{"path": s, "from": {"size": n, "digest": s}, "to": {"size": n, "digest": s}}],
  "moved": [{"from_path": s, "to_path": s, "size": n, "digest": s}],
  "absent": [{"path": s, "size": n, "digest": s}],
  "unreadable": [{"path": s, "reason": s}]
}
```

Rules:

- `diff_version` is `1` for this specification. A later change of the document format increments it.
- Every list is sorted by its path field (`from_path` for `moved`). Empty lists are `[]`.
- `bytes.added` and `bytes.removed` are the sums of the sizes of the `added` and `removed` lists. `bytes.changed_from`
  and `bytes.changed_to` are the sums of the `from` and `to` sizes of `changed`. Moves add nothing to `bytes`.
- Fields that do not apply are `null`, not omitted: `from` for a first version; `relation` when either side is `null` or
  `working`; `absent` and `unreadable` are `[]` for snapshot against snapshot.
- `absent` in a working comparison lists an entry of the `from` side; its `size` and `digest` are the entry's.
- Paths appear only in this output, never in a run record or other record.

## 9. Exit status

Decision 6 (option a, as `diff(1)`):

| Code | Meaning |
|---|---|
| 0 | The two sides do not differ. `absent` entries alone do not count as a difference. |
| 1 | The two sides differ: at least one `added`, `removed`, `changed`, `moved` or `unreadable` entry. |
| 3 | An error: no project, unknown dataset, digest not found or belonging to another dataset, version 1 document, two heads with no `--to`, missing stream or node, unknown subset, or a bad option. |

Output on success is written to standard output, even when the code is 1. Errors are written to standard error and print
no diff output.

## 10. Guarantees

- Diff is read-only. It writes no file in the dataset, no store and no record.
- Diff needs no content file for snapshot against snapshot. A missing content file never causes an error in this mode.
- Given the same documents and the same working copy contents, the output is the same. Swapping `--from` and `--to`
  swaps `added` and `removed` and reverses `changed` and `moved`; it is not a symmetric operation.
