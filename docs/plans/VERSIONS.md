# Versions: retention policy, version diff, version-keeping store

Status: design note and proposal (2026-11-21). No code is written until the decisions of section 9 are made.
Builds on `docs/plans/CONTENT-ON-DEMAND.md` (cited below as "COD <section>"): reference documents per snapshot
(COD 4), content records (COD 12), `probe` (COD 8), `get` and `get --snapshot ... --to <dir>` (COD 9, 11), `drop`
(COD 10), `history` (COD 11), store protocol 1.1 (COD 13.2) and its Decision 12 (store layouts that keep every
version). What those sections define is not repeated here. Other section references ("SNAPSHOTS 3.4", "SCHEMAS 4.1")
point into `docs/`.

## 0. Summary

Three additions, each usable alone:

1. **Retention per version.** A small, versioned policy document per dataset or area says which old versions must
   stay held somewhere: the last N, those superseded less than D days ago, and versions pinned by digest (for
   example the version a publication used). `history` shows each version as pinned, kept by policy, or droppable;
   `drop`, `put` and store pruning refuse to remove the last held copy of a kept version.
2. **Version diff.** `stradler diff` compares two snapshot documents of a dataset, or the head with the working
   copy: added, removed, changed and moved files, with counts and `--json`. It reads only snapshot documents (it works
   when no content is present), except for the working copy, which it hashes.
3. **Version-keeping store.** An optional store layout, `versioned`, that keeps every version it receives and stores
   identical content once by sha256, so N versions cost the bytes of their distinct files. `get --snapshot` serves
   old versions from it by digest. Off by default; plain stores and cold disks stay as they are.

Use cases, in stradler's own words:

- A dataset version used for a publication is pinned; later versions may replace it in the working copy and in plain
  stores, but the pin makes every removal path refuse to lose its last held copy.
- After re-sealing a dataset, the user asks what changed since the previous version, or since a version a run record
  names, without fetching any content.
- Before sealing, the user asks how the working copy differs from the head.
- A shared store keeps the whole lineage of an evolving dataset at the cost of its changed files; old versions are
  materialised for comparison with `get --snapshot --to`, and versions the policy no longer keeps are pruned.

## 1. Terms (proposed for the glossary)

| Term | Meaning |
|---|---|
| retention policy | A versioned JSON document for one dataset or one area that states which versions of a dataset's lineage must stay held (section 2.2). |
| kept version | A version that the applicable policies keep: the head, a pinned version, or a version within `keep_last` or `keep_days`. |
| droppable version | A version of the lineage that no applicable policy keeps. It may still be held; nothing removes it automatically. |
| held version | A version for which at least one location of its reference (COD 4) has a full `match` (COD 8) no older than the freshness limit (COD Decision 7), or a versioned store that holds it (section 4). The working copy never counts. |
| version diff | The comparison of two entry lists (section 3). |
| versioned store | A store whose layout is `versioned` (section 4): content objects named by sha256 plus the snapshot documents of every version it holds. |
| object | One file of a versioned store, holding the bytes of one distinct file digest. |
| prune | Removing droppable versions from a versioned store, then the objects no remaining version uses (section 4.6). |

## 2. Retention per version

### 2.1 Scope

A policy applies to one dataset or to every dataset of one area. Both may exist; a version is kept when any
applicable policy keeps it (union, Decision 2): a dataset policy can add pins and widen limits, never weaken the area's.
The head of a dataset is always kept, with or without a policy. A dataset with no applicable policy keeps only its head
(today's behaviour: nothing protects older versions).

### 2.2 Document (`retention_version` 1)

The policy is a record, not a path list: it names versions by content digest and never lists files.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `retention_version` | integer, constant 1 | yes | Version of this format. |
| `scope` | object | yes | `{"kind": "dataset", "dataset": "<project-relative folder>"}` or `{"kind": "area", "area": "<area name>"}`. |
| `keep_last` | integer, at least 1, or `null` | yes | Keep the newest N versions of the lineage, the head included. `null`: no count rule. |
| `keep_days` | integer, at least 0, or `null` | yes | Keep a version until D days after it was superseded, that is after the `created` time of the version that names it in `previous` (Decision 3). `null`: no time rule. |
| `hold_copies` | integer, at least 1 | no, default 1 | How many held locations each kept version must have (Decision 4). |
| `pins` | array of pin objects, sorted by `snapshot` | yes, may be empty | Versions kept regardless of the other rules. Allowed only when `kind` is `dataset`. |
| `previous` | digest or `null` | yes | Digest of the earlier revision of the policy for the same scope, `null` for the first. Not digested. |
| `created` | timestamp, UTC | yes | When this revision was written. Not digested. |
| `digest` | digest | yes | Digest of the canonical JSON (WORKFLOWS 10.1, 10.2) of the document without `digest`, `previous` and `created`. |

Pin object: `snapshot` (digest), `snapshot_version` (2), `reason` (free text, at most 200 characters, no paths),
`added` (timestamp). A pin of a digest that is not in the dataset's lineage is refused by the writer and reported by
`validate`.

### 2.3 Where it lives and how it is versioned

Next to the reference documents, in the record area: `<record>/retention/v1/<hex>.json`, `<hex>` the policy digest.
Revisions are versioned like snapshot documents (SNAPSHOTS 3.4, 6.2): each change writes a new document whose
`previous` names the current head of that scope; the head is the revision no other revision of the scope names; a
writer refuses a second head; documents are never edited in place and are written with a temporary file and a rename.
The policy history therefore shows when a pin was added or removed. Two heads after a merge of two clones of the
record area are an error for every command that reads the policy, until `stradler retention set` writes a revision
whose `previous` names one head and whose rules are the union of both (`--merge`).

Format changes are additive only: a later `retention_version` may add optional keys; version 1 readers refuse keys
they do not know (SCHEMAS 1 convention).

### 2.4 Commands

| Command | Effect |
|---|---|
| `stradler retention show <dataset>\|--area A [--json]` | The head policy of the scope, its revision lineage, and the effective rules for a dataset (area and dataset combined). |
| `stradler retention set <dataset>\|--area A [--keep-last N\|--no-keep-last] [--keep-days D\|--no-keep-days] [--hold-copies K] [--merge]` | Writes a new revision. |
| `stradler retention pin <dataset> sha256:<hex> --reason TEXT` / `unpin <dataset> sha256:<hex>` | Writes a new revision with the pin added or removed. |
| `stradler retention check [<dataset>...] [--area A] [--json]` | Evaluates every version of each lineage (section 2.5). |

Every write also writes a content record (COD 12) with `op` `retention_set` and the policy digest; it holds no paths.
Exit codes: 0; 1 (`check` only) when a kept version is held by fewer than `hold_copies` locations; 3 on an error
(unknown dataset or area, two policy heads, pin outside the lineage, invalid document).

### 2.5 Evaluation and `history`

For each version of the lineage (COD 11), newest first, the evaluation gives:

- `retention`: `head`, `pinned`, `kept` (with the rule: `keep_last`, or `keep_days` with the date it lapses), or
  `droppable`;
- `held`: the number of held locations (section 1) and their ids; a versioned store counts when its probe shows the
  version's document and every object present;
- `at_risk`: true for a kept version with fewer than `hold_copies` held locations.

`stradler history` gains these three fields per version and a summary line, for example
`raw/run-07  6 versions: head, 1 pinned, 2 kept, 2 droppable; at risk: 1`. The `history --json` document gains the keys
`retention`, `retention_rule`, `held`, `held_locations`, `at_risk` per version and `policy` (the policy digests used);
this is additive. `history` keeps its exit codes (COD 11); `retention check` is the command with a failing exit code, for
scheduled runs.

### 2.6 Refusals

The rule: no stradler operation removes the last held copy of a kept version. In detail:

- **drop** (COD 10) acts on the working copy, which holds only the head, and already requires `min_copies` held copies
  elsewhere. It additionally requires `hold_copies` of the effective policy when that is higher. No other change.
- **put** to a plain store (COD 13.2, later phase) replaces a file only when its sha256 equals `replace_if_sha256`. When
  the location is a held location of a kept older version and replacing a file would end that version's match there,
  `put` refuses (`retention.last_copy`) unless the version stays held at `hold_copies` other locations, or the target
  is a versioned store (which keeps the old version).
- **store prune** (section 4.6) never removes a kept version unless `--force-kept` is given and the version stays held
  at `hold_copies` other locations; it never removes the head.
- **seal**: sealing writes a new head, so a version may change from `kept` to `droppable` (count rule) and the
  outgoing head becomes an older version. Seal prints the retention changes. When the outgoing head is kept and held
  nowhere, the old bytes are already gone from the working copy at seal time (the files were edited), so a refusal
  would protect nothing; seal warns (`retention.unheld`) and the content record of the seal says so (Decision 5). The
  protection is `retention check` before editing; section 6 lists it as a risk.

## 3. Version diff

### 3.1 Command

`stradler diff <dataset> [--from sha256:A] [--to sha256:B|working] [<path>...] [--include G] [--exclude G]
[--subset NAME] [--depth size|sha256] [--stat] [--json]`

- Defaults: `--to` is the head; `--from` is the `previous` of `--to` (Decision 8: when it is `null`, the first version,
  the comparison is against an empty entry list and every entry is `added`). With `--to working` and no `--from`,
  `--from` is the head.
- `A` and `B` must be version 2 documents of the same `dataset` (any version in the record area, not only the lineage;
  the lineage relation is reported: `ancestor`, `descendant` or `unrelated`). Version 1 snapshots are refused.
- The selection options are those of COD 6.1, resolved against each side separately; a subset is resolved per
  snapshot (COD 6.2).
- `--depth` applies only to `working` (section 3.3); default `sha256`.

### 3.2 Snapshot against snapshot

Reads only the two documents and, as needed, their entry streams or tree nodes (SNAPSHOTS 5). No content file is read,
so diff works on an `expected` or `partial` dataset (COD 5.1) and without any location. For two `flat` documents the
two sorted entry lists are merged in one pass. For two `tree` documents, subtrees whose node digests are equal are
skipped without reading below them. Mixed layouts are compared as flat lists. A missing stream or node file is an
error (exit 3).

Classification per path, then a move pass:

| Category | Rule |
|---|---|
| `unchanged` | Same path, same digest. Counted, not listed. |
| `changed` | Same path, different digest. Listed with size and digest of both sides. |
| `removed` | Path only on the `from` side, not paired as a move. |
| `added` | Path only on the `to` side, not paired as a move. |
| `moved` | A removed and an added entry with the same digest (Decision 7). Pairs are formed per digest in sorted path order, one to one; leftovers stay `removed` or `added`. Files of size 0 are never paired. |

An added entry whose digest equals that of an unchanged or another entry of the `from` side carries `same_content_as`
(one path) in `--json`; it stays `added`.

### 3.3 Head against the working copy

`--to working` lists the regular files of the dataset folder with the rules of SNAPSHOTS 4.3 (the same files sealing
would record; staging folders of COD 9.3 lie outside the folder) and compares them with the `from` entries:

- depth `sha256` (default): every present file is read and hashed;
- depth `size`: a present file with the entry's size counts as unchanged; the depth is printed with the result.

Two more categories exist only here: `absent` (an entry not present in a dataset of an on-demand area, COD 3.1; in a
`present` area such an entry is `removed`) and `unreadable` (a file that cannot be read, a symbolic link, or another
non-regular entry). Moves are paired as in 3.2, among files that were hashed. Diff never changes anything and writes no
record.

### 3.4 Output

Text: a header line `diff raw/run-07 sha256:1a2b3c4d (from) -> sha256:5e6f7a8b (to), descendant`, one line per listed
entry in path order with a letter (`A` added, `D` removed, `M` changed with `size -> size`, `R old -> new` moved,
`-` absent, `!` unreadable), then a summary line:
`3 added, 1 removed, 2 changed, 1 moved, 1280 unchanged; +1.2 GiB, -0.3 GiB`. `--stat` prints only the header and the
summary.

`--json`:
`{"diff_version": 1, "dataset", "from": {"snapshot", "snapshot_version"} | null, "to": {"snapshot", "snapshot_version"}
| {"working": true, "depth"}, "relation", "selection", "counts": {"added", "removed", "changed", "moved", "unchanged",
"absent", "unreadable"}, "bytes": {"added", "removed", "changed_from", "changed_to"}, "added": [{"path", "size",
"digest", "same_content_as"}], "removed": [{"path", "size", "digest"}], "changed": [{"path", "from": {"size",
"digest"}, "to": {"size", "digest"}}], "moved": [{"from_path", "to_path", "size", "digest"}], "absent": [...],
"unreadable": [{"path", "reason"}]}`; lists sorted by path, absent values `null`. Paths appear only in this output,
never in a record.

Exit status (Decision 6): 0 when the two sides do not differ (`absent` entries alone do not count as a difference);
1 when they differ, including `unreadable`; 3 on an error (no project, unknown dataset, digest not found or of another
dataset, version 1 document, two heads, missing stream or node, unknown subset).

## 4. Version-keeping store

### 4.1 Form

A built-in store layout for stores the core reads directly (`method` `local`, COD 4.2), declared per store, plus
protocol additions so that an add-on store can offer the same service (Decision 9). `config@1.2.0` (already proposed by
COD 17 for `sidecar`) adds to `[stores.<name>]`:

| Key | Type | Meaning |
|---|---|---|
| `layout` | enum: `plain`, `versioned`; default `plain` | `plain`: the dataset tree as today (COD 13.1). `versioned`: section 4.2. |

`validate` refuses `layout` on a store whose access is an add-on (the add-on states its own capability, 4.5). Nothing
requires a versioned store: no area key, replica rule or retention rule names a layout, so a cold disk always stays a
plain store, readable without stradler.

### 4.2 Layout

Under the dataset root of the location (`<store path>/<subpath>`, COD 4.2), one folder per dataset (Decision 10):

```text
<dataset root>/
  .stradler-versioned            marker file: {"versioned_layout": 1}
  objects/sha256/<2 hex>/<62 hex>  one file per distinct file digest, plain bytes, read-only
  versions/<hex>.json            the snapshot document of each version held (verbatim)
  versions/<hex>.entries.jsonl   its entry stream, when it has one
  versions/nodes/<hex>.json      its tree nodes, when layout tree
  versions/<hex>.sha256sums      per version: "<file hex>  <entry path>" lines, sorted by path
  .lock                          present only while a put or prune runs
```

Object names are lowercase hexadecimal, so case-insensitive file systems hold them. There are no links. The
`.sha256sums` file lets a person rebuild a version with standard tools (each line names the object of a path), so the
store stays readable without stradler, though not browsable as a tree (Decision 11).

Cost: N versions occupy the sum of the sizes of their distinct file digests plus small documents. Identical content
across paths or versions of the same dataset is stored once; across datasets it is not (Decision 10).

### 4.3 Writing (put)

A version enters a versioned store through `put` (COD 7, later phase; this part depends on it, Decision 14):

1. Take the dataset root's lock (`.lock`, the rules of COD 9.5).
2. For every distinct digest of the version whose object is missing: copy the local file to a staging name in
   `objects/`, hash while writing, fsync, compare, rename onto the object name (the verified-copy routine of COD
   step 2). An existing object is trusted only at the depth the caller asked (`--verify` rehashes it).
3. Write the version's stream and nodes, then the `.sha256sums` file, then the document, each atomically (SNAPSHOTS 6.2).
   A document never names an object that does not exist.
4. Write the content record (`op` `put`, `layout` `versioned`, bytes stored, objects reused).

Only the changed bytes are transferred. An interrupted put leaves at most extra objects or staging files, never a
document with missing objects; the next put or prune removes stale staging files.

### 4.4 Reading: probe, get, verify, replicas

- **probe** (COD 8) of a versioned location for snapshot S: S is `match` when `versions/<hex of S>.json` exists and
  every entry's object exists with the entry's size, or, at depth `sha256`, hashes to its name; each distinct object is
  read once. A version whose document is absent is `missing` for every entry. The reference `last` is per snapshot, as
  today, so each version's own reference records whether this store holds it.
- **get** and **get --snapshot D --to DIR** (COD 9, 11): an entry is read from `objects/sha256/...` by its digest
  through the same verified-copy routine; the store's root path is never written. A versioned store is therefore a
  candidate for every version it holds, and COD 11's "a location keeps history only if its store keeps it" is met.
- **verify --replicas** (SNAPSHOTS 7.6) on a versioned store checks the head's objects, as probe does; its check record
  is unchanged in shape.
- **replicas**: a versioned store listed in `[replicas.<area>]` counts as a copy of the head when it holds the head;
  `min_copies` is counted as in COD 10.
- **drop** counts a versioned store like any other location.

### 4.5 Add-on stores with history

Store protocol 1.2 (additive over COD 13.2): `capabilities` already reports `history`; 1.2 adds an optional `snapshot`
parameter to `digest` (answer for that version's bytes) and an operation `versions` (`{"versions": [digest, ...]}`,
the snapshot digests the store holds for the location). `get` with `snapshot` is in 1.1. A 1.0 or 1.1 add-on answers
`unsupported_operation` and is treated as a plain store. Pruning of add-on stores is the add-on's own business and is
not in the protocol.

### 4.6 Prune

`stradler store prune <store> <dataset>... [--version sha256:<hex>]... [--force-kept] [--dry-run] [--json]`

1. Take the lock. Read the held versions (`versions/*.json`) and evaluate retention (section 2.5) against the
   project's lineage.
2. Candidates: the versions given, or by default every droppable version. The head is never a candidate. A kept
   version is refused (`retention.kept`) unless `--force-kept` and it stays held at `hold_copies` other locations.
3. Write a prune journal `<dataset root>/prune-pending.json` (versions to remove), then remove their documents,
   streams, nodes and `.sha256sums`, then delete every object no remaining version names (mark and sweep inside this
   dataset root only), then remove the journal. An interrupted prune is finished by the next prune or put from the
   journal; it can leave extra objects, never a document with missing objects.
4. Write a content record (`op` `prune`, versions removed, objects and bytes freed; no paths).

Exit status: 0; 1 when a requested version was refused; 3 on an error before any removal (lock held, two heads, store
not versioned or not configured). This is the only operation in which stradler deletes content from a location;
COD 18 keeps deletion from plain stores out of scope, and this note keeps it so.

### 4.7 Relation to Decision 12 of CONTENT-ON-DEMAND

COD Decision 12 offered (a) plain layout only, (b) a plain per-version folder layout, (c) an optional
content-addressed layout for non-cold stores, and recommended (a) now and (c) later. This note is that later (c): an
opt-in, per-store, content-addressed layout, never required and never on cold disks by rule. Option (b) is rejected
because each version would cost its full size. Option (a) stays the default for every store.

## 5. Interactions and format changes

### 5.1 With existing and planned commands

| Command | Retention | Diff | Versioned store |
|---|---|---|---|
| seal | Prints retention changes; warns `retention.unheld` (2.6). | `diff --to working` is the recommended look before sealing. | Not written by seal; `put` after seal. |
| verify | `retention check` is the policy check; `verify` unchanged. | none | `verify --replicas` reads objects (4.4). |
| probe | Probe results decide `held`. | none | Per-version match (4.4). |
| get | none | none | Serves every held version by digest (4.4). |
| drop | Requires `hold_copies` (2.6). | none | Counts as a location. |
| put | Refuses to end the last held copy of a kept version on a plain store (2.6). | none | Adds a version, changed bytes only (4.3). |
| history | Retention fields per version (2.5). | `history` names the pairs `diff` takes. | Held versions show the store. |
| validate | Policy shape, pins in lineage, one head per scope. | none | `layout` only on local stores. |
| workflows | Run records pin input digests (WORKFLOWS 9); `retention pin --from-run <run record>` pins every input version of that run (Decision 13). | `diff --from <run input digest>` shows what changed since a run. | `get --snapshot --to` restores a run's input version for a re-run outside the dataset folder. |

### 5.2 Format changes, all additive

| Item | Change |
|---|---|
| Retention document | New, `retention_version` 1, `<record>/retention/v1/`. |
| `config@1.2.0` | `[stores.<name>] layout` (with COD's `sidecar`). |
| Content record | New `op` values `retention_set`, `put` (layout), `prune`; fields for objects reused and freed. Released with COD's records if they ship together, otherwise as `content_record_version` 2 (method question 4). |
| History output | Per-version retention keys (2.5). |
| Diff output | New, `diff_version` 1. |
| Store protocol 1.2 | `digest` with `snapshot`; operation `versions`. |
| Versioned layout | New, marker `versioned_layout` 1. |

Snapshot documents, reference documents and `structure` schemas do not change.

## 6. Method questions, steps, sizes and risks

### 6.1 Method questions (settled while specifying)

1. Whether `keep_last` counts versions whose snapshot document is missing locally (a `previous` that names nothing).
2. Whether `diff` across datasets (`--any-dataset`) is useful enough to allow.
3. The order of objects in a probe at depth `sha256` for best read locality on spinning disks.
4. Whether the new content record ops can join `content_record_version` 1 (if released with COD) or need version 2.
5. Whether `store prune` should also be offered as `drop --store` for symmetry, or stay a separate verb.
6. Whether `retention check` should also flag droppable versions still held in many places (a cost report).

### 6.2 Steps in order (each shippable alone)

| # | Step | Role | Size | Depends on |
|---|---|---|---|---|
| 1 | Normative text from this note after the decisions: diff output and exit codes, retention document and JSON Schema, versioned layout, protocol 1.2; glossary terms | doc writer | S | Decisions |
| 2 | `stradler diff` snapshot against snapshot: flat inline, stream, tree with subtree skipping, moves, selection, `--json`, exit codes | Python writer | M | 1; snapshot reading only (exists) |
| 3 | `diff --to working` with depths and `unreadable`; `absent` once COD states exist | Python writer | S | 2; COD step 4 for `absent` |
| 4 | Retention documents: writer with lineage and one-head rule, `retention show\|set\|pin\|unpin`, `validate` rules | Python writer | M | 1 |
| 5 | Evaluation: kept/pinned/droppable, `held` from reference `last` and content records, `history` fields, `retention check` | Python writer | S | 4; COD steps 5 and 9 |
| 6 | Guards: `drop` with `hold_copies`, seal warning, `put` guard on plain stores | Python writer | S | 5; COD step 8, and step 13 for `put` |
| 7 | `retention pin --from-run` | Python writer | S | 4 |
| 8 | Versioned layout for local stores: `config@1.2.0` key, `put` into objects, probe, get and get --snapshot from objects, verify --replicas | Python writer | M | 1; COD steps 2, 6, 13 |
| 9 | `store prune` with journal, lock and retention guard | Python writer | M | 5, 8 |
| 10 | Store protocol 1.2 in the core and in the reference test add-on | Python writer | S | 8; COD step 7 |
| 11 | Tests as scheduler jobs: diff on duplicate and empty files, mixed layouts, large lineages; interrupted put and prune at each step; object corruption shared by versions; policy two-head merge; case-insensitive target | tester | L | 2 to 10 |
| 12 | README and command help | doc writer | S | 2 to 10 |

Steps 2 and 3 need nothing from COD beyond what exists today and can ship first. Steps 4 to 7 follow COD's probe and
history; steps 8 to 10 follow COD's `put`.

### 6.3 Risks

1. Prune is the first operation that deletes from a location; a bug loses data in a store. Mitigation: journal,
   lock, retention guard, `--dry-run` printed before any run, fault-injection tests (step 11).
2. Deduplication concentrates risk: one corrupt object damages every version that uses it. Mitigation: depth-`sha256`
   probes of versioned stores, and replicas of the head elsewhere.
3. `held` relies on recorded probe results; a location that lost content after its last match gives a false count
   (bounded by the freshness limit, as in COD 19.3 risk 3).
4. A kept version can be lost by editing the working copy before any store holds it; seal can only warn (2.6).
   `retention check` in a scheduled job is the defence.
5. `keep_days` depends on the `created` times written by other machines; a wrong clock shifts the lapse date. The
   date is shown, and pins are not affected.
6. Move pairing among duplicate contents is a convention, not a fact; the JSON states the rule so that tools do not
   over-interpret it.
7. Diff of `working` reads every byte at depth `sha256`; large datasets need it as a scheduler job.
8. Two policy heads after a merge of record area clones block the commands that read the policy until merged; this
   is chosen over a silent pick.
9. A versioned store without the tool is a set of hex-named files; recovery by hand is possible (`.sha256sums`) but
   slow. Plain stores remain the readable copies.

## 7. Out of scope

Pruning or deleting from plain stores and add-on stores; deduplication across datasets or projects; automatic put on
seal; a background job that applies retention; byte-level (delta) storage within a file; compression or encryption
of objects; diff of file contents (diff compares entries only); version 1 snapshots.

## 8. Requirement coverage

| Requirement | Section |
|---|---|
| Retention: keep last N, keep D days, pin by digest | 2.2, 2.4 |
| `history` shows pinned / within policy / may be dropped | 2.5 |
| `drop` and store pruning refuse to remove the last held copy of a kept version | 2.6, 4.6 |
| Policy is a versioned record next to the reference documents, never a path list; format and place; additive | 2.2, 2.3 |
| `stradler diff` with `--from`, `--to sha256:B\|working`, categories, counts, `--json` | 3.1 to 3.4 |
| Diff reads only snapshot documents, except `working` | 3.2, 3.3 |
| Default previous vs head; exit codes and output | 3.1, 3.4 |
| Optional store keeping every version, content stored once by sha256; `get --snapshot` serves it | 4.1 to 4.4 |
| Off by default; plain store unchanged; never required on cold disks | 4.1, 4.2 |
| Relation to COD Decision 12 | 4.7 |
| Formats and CLI; seal, verify, probe, get, drop, replicas, workflows | 2.4, 3, 4, 5 |
| Steps, sizes, risks | 6 |
| Owner decisions with options and recommendation | 9 |

## 9. Decisions for the owner

Each decision lists options and a recommendation. All are reversible until the normative text of step 1 is released.

1. **Where the retention policy lives.** (a) Its own versioned documents in `<record>/retention/v1/`, with a lineage
   per scope; (b) a key inside each reference document; (c) a table in `config.toml`. Recommendation: (a); references
   are per snapshot while a policy spans the lineage, and the config is not versioned per change, so a pin's history
   would be lost.
2. **Area and dataset policies together.** (a) Both allowed, union of what they keep (a dataset can only add);
   (b) the dataset policy replaces the area's; (c) dataset policies only. Recommendation: (a); no dataset can weaken a
   rule set for its area.
3. **Clock of `keep_days`.** (a) From supersession (the successor's `created`); (b) from the version's own `created`.
   Recommendation: (a); a version that was head for a long time still gets its full grace period.
4. **What counts as held.** (a) At least `hold_copies` (default 1) locations with a fresh full match, working copy
   excluded; (b) any recorded match, however old; (c) reuse the area's `min_copies`. Recommendation: (a); it follows
   COD's freshness rule and lets a pinned version demand more copies than the area.
5. **Seal when the outgoing head is kept and held nowhere.** (a) Warn and record; (b) refuse unless
   `--accept-unheld`. Recommendation: (a); the old bytes are already gone at seal time, so a refusal only blocks work.
6. **Diff exit codes.** (a) As `diff(1)`: 0 same, 1 differ, 3 error; (b) 0 on any successful comparison.
   Recommendation: (a); scripts can test for change without parsing output.
7. **Move detection.** (a) Exact digest, one-to-one pairing in sorted path order, empty files never paired;
   (b) no move category (added and removed only). Recommendation: (a); renames are common in reorganised datasets and
   cost nothing to detect from the entries.
8. **Default `from` of the first version.** (a) An empty list, all entries `added`; (b) an error. Recommendation: (a).
9. **Form of the version-keeping store.** (a) Built-in `versioned` layout for local stores plus protocol 1.2 for
   add-on stores; (b) add-on only; (c) none yet (COD Decision 12 (a)). Recommendation: (a); the core already reads and
   writes local stores and gains no new dependency.
10. **Object scope.** (a) Per dataset root; (b) per store, shared by all datasets. Recommendation: (a); pruning stays
    inside one lineage and one policy, at the cost of no deduplication across datasets.
11. **Head readable as a tree in a versioned store.** (a) Objects, documents and `.sha256sums` only; (b) also a plain
    copy of the head beside the objects (one more copy of the head's bytes). Recommendation: (a); readable trees are
    the role of plain stores and cold disks.
12. **Pruning.** (a) `store prune` on versioned stores only, droppable versions by default, kept ones only with
    `--force-kept` and enough other held copies; (b) no pruning in the first release. Recommendation: (a), shipped after
    `retention check` (step 9 after step 5).
13. **Versions used by runs.** (a) Manual pins plus `retention pin --from-run`; (b) automatically keep every version a
    run record names. Recommendation: (a); automatic keeping would make most versions permanent without anyone
    choosing it.
14. **Release order.** (a) Diff first (steps 2, 3), then retention (4 to 7), then the versioned store (8 to 10), each a
    separate release; (b) one release with all three. Recommendation: (a); diff needs nothing new from
    CONTENT-ON-DEMAND, and the versioned store waits for its `put`.
