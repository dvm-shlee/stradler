# Content on demand: expected snapshots, references, probe, get, drop, status, history

Status: design note and proposal (2026-11-20). No code is written until the decisions of section 20 are made.
Baseline: stradler main `85f9e14`. Terms are those of `GLOSSARY.md`; section references such as "SNAPSHOTS 4.3",
"SCHEMAS 4.1", "ADDONS 8" and "WORKFLOWS 5.3" point into the documents of `docs/`. Terms new in this note are in
section 2 and are proposed for the glossary.

## 0. Summary

A project can declare a dataset by the snapshot of the content meant to be there before any byte of it is present
(an expected snapshot), say where that content can be found (a reference document with several locations), ask each
location without transferring content whether it holds that content (probe), and then bring in all of it or a chosen
part (get) as plain, verified files. Content that is held elsewhere in enough verified copies can be removed locally
(drop). Absent content leaves nothing in the dataset folder: no links, no stubs, no placeholder files. Every probe,
get and drop writes a record without file paths or contents. The state of every dataset (expected, partial, present,
differs) is computed from the snapshot and the disk, and shown by `stradler status`.

Use cases, in stradler's own words:

- Data still on an acquisition machine is declared now from the snapshot document written there; tools and
  workflows see it as expected, and it comes in once the machine is reachable and a probe matched.
- Data on a partner's server is declared with the partner's snapshot; a probe shows whether the partner still holds
  it; only the part an analysis needs is fetched.
- Data not yet deposited in an archive is declared with its snapshot; the archive location is added to the reference
  when the deposit exists, and from then on it is one more verified copy.
- A large sealed dataset is kept on a shared store, on an offsite store and on cold external disks; a working copy
  holds only the sessions it analyses and drops them afterwards.
- An earlier version of a dataset (through `previous`) is materialised into a separate folder for comparison, if a
  location still holds its bytes.

## 1. What exists and what is missing

Exists on main: `[true_sources.<area>]` and the area keys `store` and `source` (SCHEMAS 4, 5); `[replicas.<area>]`
with `stores` and `min_copies` (`config@1.1.0`, SCHEMAS 4.1); store and remote add-ons with one operation, `digest`
(ADDONS 8), used by `verify --replicas` (SNAPSHOTS 7.6); snapshot format 2 with `previous` lineage (SNAPSHOTS 3.4);
sealing that refuses symbolic links (SNAPSHOTS 4.3) and journals its permission changes (SNAPSHOTS 7.5); check
records in `<record>/checks/` (SNAPSHOTS 7.4); adopt copy mode with temporary file, size and sha256 check, rename
(`ADOPT.md`).

Missing: any way to write a snapshot for content that is not in the project; any notion of a dataset folder that holds
only part of its snapshot; any operation that transfers content from or to a store; any record of where a snapshot's
content can be found other than the per-area store declarations.

## 2. Terms (proposed)

| Term | Meaning |
|---|---|
| working copy | The dataset folder inside a project, on one machine. It may hold all, part or none of its snapshot's files. |
| expected snapshot | A version 2 snapshot document (SNAPSHOTS 3) written for content that is, at the time of writing, not (or not all) in the working copy. It is a normal snapshot document; "expected" describes the working copy, not the document. |
| dataset state | One of `expected`, `partial`, `present`, `differs`, computed by comparing the working copy with the head snapshot (section 5). Never stored as the source of truth. |
| reference document | A versioned JSON document in the record area, one per snapshot, that lists the locations known to hold that snapshot's content and the named subsets of it (section 4). |
| location | One entry of a reference document: a route by which the content can be read (a store name, or a URL or archive identifier resolved by an add-on), with an optional subpath. |
| probe | A check of one location against a snapshot without transferring content (section 8). |
| selection | A set of snapshot entries chosen by paths, globs, a named subset and size limits, resolved against the snapshot's entry list before any transfer (section 6). |
| subset | A named selection, declared in a reference document and reused by `get`, `drop`, `status` and workflow inputs. |
| fetch | Copying one entry from a location into the working copy, verified and renamed into place (section 9). |
| drop | Removing entries from the working copy when enough other verified copies exist (section 10). |
| content record | The record that every probe, get and drop writes in the record area (section 12). |

## 3. Declaring content without holding it (requirement 1; addendum 2.1, 2.2)

### 3.1 On-demand areas

`structure@1.2.0` adds one optional area key (Decision 1):

| Key | Type | Meaning |
|---|---|---|
| `content` | enum: `present`, `on_demand` | `present` (default, today's behaviour): every dataset of the area is expected to be complete in the working copy. `on_demand`: a dataset of the area may be expected, partial or present; `get` and `drop` act on it. |

Rules checked by `validate`: `on_demand` requires mutability `sealed` (the only mutability that has snapshots
today, SNAPSHOTS 7.5); it is not allowed together with the area key `store` (such an area already lives outside the
project); it is not allowed for base roles `record`, `code`, `recipe` and `agent` (these hold the metadata layer,
code and the snapshot documents themselves). `structure@1.0.0` and `structure@1.1.0` are unchanged.

### 3.2 Expected snapshots

A dataset is declared by writing its snapshot document before the content is present:

- `stradler snapshot <folder> --out <file> [--layout flat|tree] [--stream]` writes a version 2 snapshot document of
  any plain folder, without a project and without changing anything (no sealing, no permission change). It is the
  tool for the acquisition machine or the partner: run where the content is, send the small document.
- `stradler expect <dataset> --snapshot-file <file> [--provenance-file F] [--grade G]` imports such a document into
  `<record>/snapshots/v2/` of the project, for a dataset folder of an on-demand area. It recomputes the content digest
  from the entries (a document that does not reproduce its digest is refused), replaces `dataset` with the project
  path given, and sets `previous` to the current head of that dataset (lineage rule of SNAPSHOTS 3.4; a second head is
  refused). It writes a reference document for the snapshot (section 4) and creates no file and no folder in the
  working copy.
- `stradler expect <dataset> --from-location <location>` (later phase, Decision 11) builds the entries from a
  location's `list` and `digest` answers. The identity then comes from what the location reports; the provenance
  records this.

Identity is fixed by the snapshot content digest, never by a location: a location that later holds other bytes is
`mismatched`, it does not change the dataset.

Content not known yet (no file exists anywhere, so no digest can exist) cannot be expected; it is out of scope
(section 18).

### 3.3 No link look (addendum 2.1)

Absent content leaves nothing in the dataset folder that looks like a file: no symbolic links, no stubs, no pointer
files, no zero-length placeholders with real names. An expected dataset may have no folder at all. The working copy
holds only real, verified files at their snapshot paths, so any tool that lists the folder sees exactly what is
present. Temporary files of a transfer are written outside the dataset folder (section 9.3).

## 4. Reference documents (addendum 1)

### 4.1 Location and granularity

One reference document per snapshot, at `<record>/references/<hex>.json`, `<hex>` the content digest without prefix
(Decision 2). It is written with a temporary file and a rename (SNAPSHOTS 6.2). Because it is named by the snapshot,
the reference of a dataset version never mixes with another version's. When `expect` or `seal` writes a new version
whose `previous` has a reference, the store locations and the subsets of the previous reference are carried forward
as unprobed locations (their last result is empty until a probe of the new snapshot).

### 4.2 Document (`reference_version` 1)

| Key | Type | Required | Meaning |
|---|---|---|---|
| `reference_version` | integer, constant 1 | yes | Version of this format. |
| `dataset` | string, project-relative folder | yes | The dataset folder, as in the snapshot document. |
| `snapshot` | digest | yes | Content digest of the snapshot the locations resolve to. |
| `snapshot_version` | integer, 2 | yes | Format of that snapshot (version 1 snapshots are out of scope). |
| `locations` | array of location objects, sorted by `id` | yes, may be empty | Where the content is known to be. |
| `subsets` | object, name to selection object | no | Named subsets (section 6.2). |

Location object:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | name (`^[A-Za-z0-9_-]+$`) | yes | Stable name of the location in this document. |
| `kind` | enum: `store`, `url`, `archive` | yes | `store`: a `[stores]` name; `url`: a URL; `archive`: an external archive identifier such as a DOI. |
| `store` | name | when `kind` is `store` | The `[stores]` entry. Its real path or endpoint is in `local.toml` (SCHEMAS 6.2), never here. |
| `url` | string | when `kind` is `url` | URL of the dataset root. Refused if it carries user information or a query string (no credentials, no tokens). |
| `archive_id` | string | when `kind` is `archive` | Identifier in the archive's own scheme, for example `doi:10.xxxx/yyyy`. |
| `subpath` | relative path | no | Folder of the dataset root below the location root. Default: the `dataset` path, which keeps the rule of SNAPSHOTS 7 (`<store path>/<dataset>`). |
| `method` | enum: `local`, `addon` | yes | `local`: the core reads the location as a directory (a store with `path` in `local.toml`). `addon`: the transfer goes through a store or remote add-on: the store's own add-on for `kind` `store`, or the add-on named by `addon` for `url` and `archive`. Other methods (rsync, http) are add-ons, not core methods (Decision 10). |
| `addon` | name | when `method` is `addon` and `kind` is not `store` | `[addons]` entry of kind `store` or `remote` that resolves the URL or archive id. |
| `added` | timestamp, UTC | yes | When the location was added. |
| `origin` | enum: `true_source`, `replica`, `declared` | yes | Why the location is listed (section 4.3). |
| `last` | object or null | yes | Summary of the newest probe or check of this location for this snapshot (section 4.4): `time`, `result` (`match`, `partial`, `mismatched`, `unreachable`), `counts`. `null` before the first probe. |

A reference document never holds credentials, real local paths, endpoints, or any file path of the content. Access
details stay in `local.toml` and add-on settings, which differ per machine; the same reference works on every machine,
and a location whose store has no `local.toml` entry on this machine is reported as `unconfigured` there.

### 4.3 Relation to `[true_sources]` and `[replicas]`

Kept separate, with store locations derived from them (Decision 3):

- `config.toml` states policy per area: which store is authoritative (`[true_sources]`, `source`), which stores must
  hold copies and how many (`[replicas]`, `min_copies`). It does not change.
- The reference document states facts per snapshot: where this exact content is known to be, and what the last probe
  found. When a reference is created, the area's true source and replica stores are written as `store` locations
  with `origin` `true_source` or `replica`; other locations (a partner's server, an archive, an acquisition machine
  exposed as a store) are added with `stradler reference add` and have `origin` `declared`.
- `validate` reports a reference that lacks a store location the config requires, and a config store that no
  reference of the area lists. It never edits either.
- `verify --replicas` (SNAPSHOTS 7.6) keeps its behaviour and its check records; it additionally updates `last` of
  the matching `store` locations of the head's reference. `probe` (section 8) is the general form, for every location
  kind.

### 4.4 The `last` summary and link rot

The content records (section 12) and check records are the authority; `last` is a summary copied from the newest of
them, rewritten atomically by `probe`, `get`, `drop` and `verify --replicas` (Decision 4). If the two disagree, the
records win and `stradler reference --repair` rewrites `last` from them.

Link rot is handled by having several locations and probing each one: a location that becomes unreachable or
mismatched keeps its entry with its last result, so the history of when it stopped matching is in the records. A
location is removed only by `stradler reference remove <dataset> <id>`, which writes a record. Moved content gets a
new location. `status` shows the age of each location's last match, so an old match is visible. Scheduled probes
(cron or a scheduler job that runs `stradler probe`) keep the summaries fresh; stradler itself does not run in the
background.

### 4.5 In git or not

Reference documents live in the record area next to the snapshot documents and are versioned with them (in git when
the project keeps its record area in git; stradler does not run git). They hold logical names only. `local.toml`
(git-ignored) keeps paths and endpoints; add-on settings keep the rest; credentials stay with the add-on. The
presence of content on one machine is never in git (section 5.2).

## 5. Dataset state and status (addendum 2.2, 3.2)

### 5.1 States

For a dataset of an on-demand area, the state compares the working copy with its head snapshot (SNAPSHOTS 3.4):

| State | Condition |
|---|---|
| `expected` | No entry of the snapshot is present; nothing else lies in the folder (or the folder does not exist). |
| `partial` | Some entries are present and match; the rest are absent; no other file lies in the folder. |
| `present` | Every entry is present and matches. |
| `differs` | At least one present file does not match its entry (size or sha256), cannot be read, is a symbolic link or other non-regular entry, or a file lies at a path that is not in the snapshot. |

A file "matches" at depth `size` when its size equals the entry, and at depth `sha256` when its bytes hash to the
entry digest. `status` uses depth `size` by default and `sha256` with `--verify`; the depth is reported with the
state. `get` checks every file it writes at depth `sha256`, and a dataset becomes `present` in the get record only
when every entry has been checked at depth `sha256` in that run or is reported `present (size)` otherwise (section
9.4). A dataset of a `present` area is reported as today: `present` or `differs`.

### 5.2 Where presence is recorded

Presence is the file system itself: an entry is present when a regular file lies at its path. Nothing in git records
which files a machine holds, because that differs per machine and would conflict between clones. The content records
(section 12) say what each get and drop did, without paths. No per-file cache is kept in v1 (Decision 5); hashing
is on demand. A cache, if later wanted, is machine-local and git-ignored.

### 5.3 `stradler status`

`stradler status [<dataset>...] [--area AREA] [--subset NAME] [--verify] [--json]`

- No target: every dataset with a v2 head in every on-demand area. A dataset path, an area, or both narrow it.
- Per dataset: state and depth, head snapshot (short digest), counts present / expected entries, bytes present /
  expected, the subsets of the reference that are fully present, stale staging folders (section 9.3), and per
  location of the reference its `last` result and age. `--subset NAME` reports the state of that selection only.
- Text form, one line per dataset, for example:
  `raw/run-07  partial (size)  412/1288 files  51.2/160.4 GiB  subsets: anat  locations: shared match 2d, partner unreachable 9d`
- `--json`: `{"status_version": 1, "datasets": [{"dataset", "snapshot", "state", "depth", "counts": {"expected",
  "present", "absent", "mismatched", "added", "unreadable"}, "bytes": {"expected", "present"}, "subsets_present",
  "staging", "locations": [{"id", "result", "time"}]}]}`; lists sorted, absent values `null`.
- Exit status: 0 when no dataset `differs`; 1 when one does; 3 on an error (no project, invalid document, two heads,
  unknown dataset or subset). `expected` and `partial` are declared states, not failures.

`stradler describe --json` gains the area key `content` (this raises `describe_version` to 3, DESCRIBE "Version
rule"); it reports no presence.

## 6. Selections and named subsets (addendum 3)

### 6.1 Selection grammar

Every `get`, `drop` and `status` takes the same selection options; they are resolved against the snapshot's entry
list before any transfer or removal:

- positional `<path>` after the dataset: an entry path or a folder prefix, relative to the dataset folder;
- `--include GLOB` and `--exclude GLOB` (repeatable): glob patterns relative to the dataset folder, with the same
  semantics as the default handler's `include`/`exclude` (WORKFLOWS 3.4);
- `--subset NAME` (repeatable): a named subset of the reference;
- `--max-file-size SIZE`: keep only entries of at most this size;
- `--max-total SIZE`: refuse (exit 3, nothing transferred) when the resolved selection exceeds this many bytes; it
  never truncates silently.

Resolution: the union of paths, includes and subsets (all entries when none is given), minus excludes, then the file
size filter, then the total check. A path or a subset that matches no entry is an error. Sizes use the suffixes `K`,
`M`, `G`, `T` (WORKFLOWS 3). `--dry-run` prints the exact entry list and byte totals and changes nothing.

### 6.2 Named subsets

A subset is a selection object in the reference's `subsets`: `{"include": [globs], "exclude": [globs],
"max_file_size": SIZE, "description": text}`. Typical subsets are one modality, one session, or the files a
standard analysis reads. `stradler reference subset <dataset> <name> --include ... [--exclude ...]` writes it; subsets
are carried forward to the next version's reference (section 4.1). A subset is resolved per snapshot, so it names the
same intent across versions while its exact files follow the entries.

## 7. CLI overview (exit codes in each section)

| Command | Purpose |
|---|---|
| `stradler snapshot <folder> --out F` | Write a snapshot document of a plain folder, outside any project (section 3.2). |
| `stradler expect <dataset> --snapshot-file F` | Declare an expected snapshot and create its reference (section 3.2). |
| `stradler reference show\|add\|remove\|subset <dataset> ...` | Read and edit the head's reference (section 4). Every add and remove writes a content record. |
| `stradler probe <dataset> [--location ID]... [selection]` | Check locations without transferring content (section 8). |
| `stradler get <dataset>... [selection] [--from ID] [--snapshot D --to DIR] [--dry-run]` | Fetch (section 9). |
| `stradler drop <dataset>... [selection] [--dry-run]` | Remove local content safely (section 10). |
| `stradler status [<dataset>...]` | States (section 5.3). |
| `stradler history <dataset>` | Lineage and where each version is held (section 11). |
| `stradler put <dataset> --to ID` | Copy the working copy to a store location (later phase, Decision 11). |

All commands take `--json` (one versioned document) and work on several datasets with `--area AREA` or `--all`.

## 8. Probe (addendum 2.3)

`stradler probe <dataset> [--location ID]... [selection] [--json]`

For each chosen location (default all locations of the head's reference) and the selected entries (default all):

- `method` `local`: the core reads the files under the location's dataset root, checks size and sha256 of each entry
  (SNAPSHOTS 7.2 rules), and never writes there.
- `method` `addon`: the core sends the entry paths to the add-on's `digest` operation in batches of at most 500
  (ADDONS 8) and compares size and sha256. No content is transferred.
- Per location, counts of entries `matching`, `missing`, `mismatched`, `unreadable`, and a result: `match` (every
  selected entry matches), `partial` (some missing, none mismatched or unreadable), `mismatched` (at least one
  mismatched or unreadable), `unreachable` (the location could not be reached, or is `unconfigured` on this machine).
- A content record is written per (location, snapshot) and `last` of the location is updated (a probe of a selection
  updates `last` only when the selection is the whole snapshot; otherwise the record holds the selection, section 12).

Exit status: 0 when at least one location `match`es the selection; 1 when none matches and at least one is
`mismatched`; 2 when none matches and none is mismatched (missing, partial or unreachable only); 3 on an error.

Automatic get after the first successful probe (addendum 2.5) is not in v1. The design keeps room for it: a later
`reference_version` may add an optional `watch` object (selection, trigger `first_match`), and a scheduled
`stradler probe --then-get` would run the get of section 9 under the same rules. v1 is manual.

## 9. Get (requirement 1; addendum 1.2, 2.4, 3)

### 9.1 Command

`stradler get <dataset>... [<path>...] [--include G] [--exclude G] [--subset NAME] [--max-file-size S]
[--max-total S] [--from ID]... [--reprobe | --no-probe] [--verify] [--dry-run] [--json]`

and, for history, `stradler get <dataset> --snapshot sha256:<hex> --to <dir>` (section 11).

### 9.2 Procedure

1. Resolve the head snapshot (or `--snapshot`) and the reference; refuse a dataset outside an on-demand area, with
   two heads, or without a reference. Resolve the selection (section 6).
2. Take the dataset lock (section 9.5). Remove stale staging folders of this dataset left by an earlier run.
3. Determine what is missing: for each selected entry, a regular file at its path whose size matches (or whose sha256
   matches, with `--verify`) is already present and is not fetched (incremental get). A file at an entry path that
   does not match is a `conflict`: it is never overwritten, it is reported, and the run ends with exit 1 after the
   other files (the user resolves it, for example by `drop --force-local` of that file after inspection, section 10).
4. Choose locations (Decision 6): candidates are locations whose `last` result is `match` for this snapshot, in this
   order: `--from` if given, then the machine's preference list (`[fetch] prefer` in `local.toml`, optional), then
   `true_source`, `replica`, `declared` locations, each group by `id`. When no candidate has a full match, `get`
   re-probes the missing entries on the locations whose `last` is `partial`, `null` or older than the freshness limit
   (default behaviour; `--no-probe` refuses instead, exit 2; `--reprobe` re-probes all candidates first). An entry is
   fetched only from a location where it was confirmed matching by a full-match `last` or by this run's re-probe.
5. Check free space: the sum of the sizes to fetch plus a margin (1 percent, at least 64 MiB) against the free space
   of the dataset's file system; refuse before any transfer when short (exit 4).
6. Fetch each entry (section 9.3), in sorted path order, falling back to the next candidate location on an
   unreachable location or a digest mismatch.
7. Restore permissions: fetched files and the folders created for them lose their write bits, as after sealing
   (SNAPSHOTS 7.5); on a file system without permission bits (exFAT) this step is skipped and reported.
8. Post-check against the snapshot (addendum 2.4): every entry of the selection is checked at depth `sha256` (fetched
   files were hashed while written and are not read again); the rest of the dataset at depth `size`, or `sha256` with
   `--verify`. The resulting state is reported and written to the content record. Only a full match of every entry
   makes the dataset `present`.
9. Write one content record per dataset (section 12), release the lock, print the summary.

### 9.3 Atomic fetch and staging

- Each entry is written to a staging folder, then renamed onto its final path. A partial or failed fetch never leaves
  a file at its final path.
- The staging folder is `<parent of the dataset folder>/.stradler-incoming-<first 16 hex of sha256(dataset)>/`, outside
  the dataset folder, so that a transfer in progress never puts anything into the dataset folder (section 3.3) and
  `verify` and `seal` never see temporary files. It is on the same file system as the dataset folder in every normal
  layout, so the rename is atomic; if the rename fails with a cross-device error (the dataset folder is itself a
  mount point), the run stops for that dataset with an error and names the problem (Decision 13).
- The bytes are hashed while they are written; after the write the file is flushed to disk (`fsync`), its size and
  sha256 are compared with the entry, and only then is it renamed, after its parent folders are created. A mismatch
  deletes the staged file. An add-on never writes the final path; it writes only the staging path the core gives it,
  and the core hashes that file itself (an add-on's own claim is never trusted).
- Write bits of read-only parent folders (a dataset that was sealed or fetched before) are added for the rename and
  removed afterwards. The original bits are written first to a journal, `<record>/content/pending/<sha256 of the
  dataset>.json`, as seal does (SNAPSHOTS 7.5); a later `get`, `drop` or `status --repair` restores them after an
  interruption.

### 9.4 Per-entry outcomes and output

Outcomes: `present` (already there), `fetched`, `conflict` (a different file lies at the path), `mismatched` (every
candidate location gave other bytes), `not_held` (no candidate holds the entry), `unreachable` (no candidate could be
reached), `no_space`, `failed` (a local write error).

Text output: one summary line per dataset, for example
`get raw/run-07 (subset anat): 96 fetched (12.0 GiB) from shared, 316 present, 0 failed; state partial 412/1288`;
`-v` adds one line per entry. `--json`: `{"get_version": 1, "datasets": [{"dataset", "snapshot", "selection",
"counts": {outcome: n}, "bytes": {"fetched", "present"}, "locations": [{"id", "files", "bytes", "result"}],
"state", "depth", "problems": [{"path", "outcome", "location", "message"}]}]}`. Paths appear in the output for the
caller and never in the record.

`--dry-run` resolves everything up to step 5 (including the candidate choice from recorded results, without a
re-probe) and prints the exact entries, bytes and chosen locations.

Exit status, the first that applies: 3 for an error before any transfer (usage, unknown dataset or subset, no on-demand
area, invalid snapshot or reference, two heads, selection above `--max-total`, lock held); 4 for a local write
problem that stopped the run (no space, permission, I/O error); 1 for a content problem (`conflict`, `mismatched`,
`not_held`); 2 when entries remain only because locations were unreachable or not probed; 0 when every selected entry
is present and matches.

### 9.5 Concurrency

One lock per dataset, a file created with exclusive create in the staging folder, holding host, process id and start
time. A second `get` or `drop` on the same dataset waits up to `--wait SECONDS` (default 0) and then exits 3. A lock
of a dead process on the same host is broken automatically; a lock from another host is broken only with
`--break-lock`. Parallel gets of different datasets (for example one scheduler array task per dataset) do not share
a lock.

## 10. Drop (requirement 2; addendum 1.4, 3.4)

`stradler drop <dataset>... [selection] [--min-copies N] [--reprobe] [--keep-manifest | --drop-manifest] [--dry-run]
[--json]`

For each selected entry present in the working copy:

1. The local file must match its entry at depth `sha256` (it is read and hashed). A file that does not match is not
   the snapshot's content and may be the only copy of other bytes: it is refused (`local_differs`), never removed.
2. Count copies elsewhere for this entry: the distinct locations of the reference whose last result confirms the
   entry, that is a `last` result of `match` no older than the freshness limit (Decision 7), or a confirmation of
   this entry by a probe made in this run (`--reprobe`, or automatically for locations whose `last` is `partial` or too
   old). The working copy itself never counts. Two locations that resolve to the same store count once.
3. The entry is removed only when the count is at least `min_copies` (the rule of git-annex's `numcopies`, per file):
   `min_copies` of `[replicas.<area>]`, or `--min-copies N` when it is higher; when the area declares none, 1
   (Decision 8). `--min-copies` can only raise the number.
4. Removal: the folder write bit is added, the file unlinked, the bit restored (with the journal of section 9.3);
   folders left empty are removed, except the dataset folder itself. Nothing is left in place of the file.
5. The area's manifest file (the `manifest` key of the area) at the dataset root is kept unless `--drop-manifest`,
   so that the metadata layer stays readable.

A drop of a whole dataset leaves the state `expected`. Drop never removes anything from a location; it never acts on
an area that is not on demand, on a dataset without a v2 head, or on files that are not entries of the head.

Output as for `get`, with outcomes `dropped`, `absent`, `local_differs`, `too_few_copies`, `unreachable` (copies
could not be counted). Exit status: 3 for an error before any removal; 1 when an entry was refused (`local_differs`,
`too_few_copies`); 2 when entries were kept only because locations were unreachable; 0 otherwise. Every drop writes a
content record.

Races: drop does not lock remote content. Between the count and the unlink a location could lose its copy through an
action outside stradler; stradler itself has no operation that deletes from a location (section 18), so two working
copies dropping at the same time cannot remove a location's copy. The freshness limit bounds how old a match may be.

## 11. History (requirement 3)

`stradler history <dataset> [--json]` follows `previous` from the head (SNAPSHOTS 3.4) and lists per version: content
digest, `created`, `file_count`, `total_size`, whether a reference exists, and per location its `last` result for
that version. For the head it adds the dataset state. Exit 0, or 3 on an error (two heads, a `previous` that names no
document).

`stradler get <dataset> --snapshot sha256:<hex> --to <dir> [selection]` materialises an earlier version (or any
snapshot of the dataset's lineage) into `<dir>`, a folder outside every area, as plain files with the same atomic,
verified procedure. It never replaces the working copy of the dataset folder, whose content always belongs to the
head (Decision 9). Locations are those of that version's reference; a `store` location of the current reference is
tried as well, because files unchanged between versions still match their entries there. Per entry, a location holds
an older version exactly when its bytes at that path still hash to the old entry; otherwise the entry is `not_held`.
A location keeps history only if its store keeps it (an add-on store with its own object versions may serve old bytes
by digest through `get`, section 13; a plain folder holds whatever was copied last). Store layouts that keep every
version are a later decision (Decision 12).

## 12. Content records (addendum 1.3, 3.3)

Every `probe`, `get`, `drop`, `reference add` and `reference remove` writes one record, in
`<record>/content/<UTC time YYYYMMDDTHHMMSSZ>-<op>-<hex>.json` (a numeric suffix when the name exists; never
overwritten), like the check records of SNAPSHOTS 7.4. Members (`content_record_version` 1):

| Key | Meaning |
|---|---|
| `content_record_version` | Integer 1. |
| `op` | `probe`, `get`, `drop`, `reference_add`, `reference_remove`. |
| `time_started`, `time` | UTC timestamps. |
| `dataset` | The dataset folder (the same value as in the snapshot document). |
| `snapshot`, `snapshot_version` | The snapshot acted on. |
| `selection` | `null` for the whole snapshot, else the selection as given: subset names, include and exclude patterns, size limits, and the count of positional paths (not the paths). |
| `locations` | Per location used or probed: `id`, `kind`, `method`, `files`, `bytes`, `result` (`match`, `partial`, `mismatched`, `unreachable`, `ok`). No store path, endpoint or setting. |
| `counts` | Entry counts by outcome (sections 8, 9.4, 10). |
| `bytes` | Bytes transferred or removed. |
| `result` | `ok`, `partial`, `mismatch`, `unreachable`, `refused`, `error`. |
| `state_after` | For get and drop: state, depth, present and expected entry counts. |
| `stradler_version` | Version of the writer. |

No file path, file name or content is stored. Records are append-only; a mistake is corrected by a later record.

## 13. Stores, add-on protocol additions and cold storage

### 13.1 Local stores

A location with `method` `local` (a store with `path` in `local.toml`) is read and written by the core: probe as in
SNAPSHOTS 7.2, get as a verified copy, put (later phase) as a verified copy into a staging name in the store and a
rename. The dataset root is `<store path>/<subpath>`, plain files at their entry paths. No object layout, no links,
no database: a cold external disk holds exactly the dataset tree and is readable without stradler.

Optional on a store (Decision 11, with `put`): a sidecar folder `<store path>/<subpath>/../.stradler/` with the
snapshot document and a `SHA256SUMS` file in the format of `sha256sum -c`, so that a cold disk can be checked
without stradler. It is off for a store with `sidecar = false` in `[stores.<name>]` (`config@1.2.0`).

exFAT and similar targets: no permission bits (step 7 of section 9.2 skipped), no links (none are used), case-
insensitive names (two entries that differ only in case cannot both be stored; `put` refuses with `name_conflict`, and
`probe` reports the dataset as not storable there), forbidden characters (`"*:<>?\|` and control characters: `put`
refuses), coarse timestamps (nothing relies on modification times). Unicode normalization stays an open point of
SNAPSHOTS 10.7.

### 13.2 Store protocol 1.1 (additive)

Protocol minor version 1.1 adds operations for add-ons of kind `store` and `remote`. A 1.0 add-on stays valid: it
answers `unsupported_operation`, and the core then reports the operation as not supported for that location. A core
sends a 1.1 operation only after `capabilities` lists it.

| Operation | Purpose | Params | Result |
|---|---|---|---|
| `capabilities` | Discover the store operations. | none | `{"kind", "version", "operations": [...], "max_paths": int <= 500, "history": bool, "locate": [...]}`; `locate` lists location kinds it resolves (`store`, `url`, `archive`). |
| `digest` | Unchanged (ADDONS 8). | as today, plus optional `location` | as today. |
| `list` | List files under a prefix, with sizes. | `store`, `store_settings`, `location` (the location object without `last`), `prefix`, `cursor` | `{"files": [{"path", "size"}], "next_cursor"}` |
| `get` | Write the bytes of the given paths to staging files. | `store`, `store_settings`, `location`, `items`: at most 500 `{"path", "size", "sha256", "dest"}`, `snapshot` (optional, for stores with history) | `{"items": [{"path", "status": "ok" \| "missing" \| "mismatched" \| "failed", "message"}]}` |
| `put` (later phase) | Store files atomically on the store side. | `items`: at most 500 `{"path", "size", "sha256", "source", "replace_if_sha256"}` | `{"items": [{"path", "status": "stored" \| "exists" \| "conflict" \| "failed", "message"}]}` |

Rules: `dest` is an absolute path in the core's staging folder that does not exist yet; the add-on creates it, writes
it, closes it and writes nothing else. `get` may report `mismatched` when it checks the bytes itself; the core hashes
the file in every case. `paths` and `items` hold at most 500 entries per request, as `digest` (addendum 3.6); the core
sends larger selections in batches. `put` never overwrites a file at a path unless its current sha256 equals
`replace_if_sha256` (the previous version's entry), so unknown content is never destroyed; an identical file is
`exists`. `location` lets one add-on resolve URLs or archive ids. `settings` and `store_settings` never hold
credentials (ADDONS 1.3).

Error codes: the envelope codes of ADDONS 1.4 keep their meaning; 1.1 adds `no_space` (the store has no room, for
`put`) and `unreachable` (the add-on could not reach its backend). A 1.0 core never receives them because it sends no
1.1 operation. The core maps `backend_error` and `unreachable` to `unreachable`, as `verify --replicas` does.

### 13.3 Error cases

| Case | Behaviour |
|---|---|
| Location unreachable (offline disk, network down, add-on error) | Next candidate location; entries left are `unreachable`; exit 2 if nothing worse. Probe records `unreachable`. |
| Location not configured on this machine | Reported `unconfigured` (as `unreachable`); never an error for the other locations. |
| Digest mismatch on fetch | Staged file deleted; next candidate; the location's mismatch is in the content record; if every candidate mismatches, `mismatched`, exit 1. `status` suggests a probe. |
| Disk full before start | Free-space check refuses, exit 4, nothing written. |
| Disk full during a write | Staged file deleted; the run stops; files already renamed are complete and verified; record result `error`; exit 4. |
| Interrupted fetch (kill, node failure, power) | No file at a final path is partial (staging plus rename). The next run removes the stale staging folder, restores folder bits from the journal and fetches only what is missing. A stale lock is broken as in section 9.5. |
| Conflict at a final path | Never overwritten; exit 1. |
| Add-on violates the protocol (writes elsewhere, bad response) | `internal` (ADDONS 1.5); the batch counts as failed; the staged files are checked or deleted. |
| Two heads, invalid snapshot or reference | Error before anything changes, exit 3. |

## 14. Interaction with seal, verify, replicas, describe, adopt and workflows

- **seal.** Sealing a dataset in an on-demand area requires state `present` at depth `sha256` when a head exists;
  otherwise it is refused (`content.absent`). Without this rule, sealing a partial working copy would write a new head
  with fewer files and a `previous` pointing at the complete version, a silent loss in the lineage. A new dataset
  with no head is sealed as today, and its reference is created with the area's store locations.
- **verify.** `stradler verify <dataset>` is unchanged: absent entries are `missing`, so a partial dataset `differs`.
  A new flag `--present` checks only the entries present locally, reports absent ones as a separate count and writes
  no check record (it is not a check of a complete copy). `verify --store` and `verify --replicas` are unchanged and
  also update the `last` summary of the matching store locations (section 4.3).
- **replicas.** `[replicas]` keeps its meaning for `describe` and `verify --replicas`. `drop` uses its `min_copies`,
  counted over reference locations (section 10).
- **describe.** Adds the area key `content` (`describe_version` 3). No presence, no locations.
- **validate.** Checks the rules of sections 3.1 and 4.3 and the shape of reference documents.
- **adopt.** Copy mode and get share one verified-copy routine (staging, hash while writing, fsync, rename).
- **workflows.** A workflow reads plain files, so its inputs must be present before plan and execute:
  - The planner resolves, for each input in an on-demand area (the `inputs` dataset, `fixed` and `named` paths), the
    entries the step needs: by default the entries matched by the input's filter; with the new optional key
    `subset` (in the `inputs` block and in a step `in` entry; added to the recipe schema additively), only that
    subset's entries (addendum 3.5).
  - If any of those entries is absent, the plan is refused with code `content.absent`, the dataset, the count of
    absent entries and bytes, and the exact `stradler get` command; files outside the needed set may be absent.
    This prevents the data handler from silently finding fewer units on a partial copy. With the run option
    `fetch_inputs` (Decision 14) the planner runs that get first, under all rules of section 9.
  - An output path covered by the head snapshot of an on-demand sealed area whose entry is absent is `not-run` with
    reason "output content held elsewhere; run stradler get", never planned again: re-running would write new bytes
    into a sealed dataset.
  - Run records keep recording input snapshots by digest (WORKFLOWS 9); a task's input digest (WORKFLOWS 10.3) reads
    the bytes, which are present by the rule above.

## 15. Links and SNAPSHOTS 4.3

Content is materialised as real files. A link tree would conflict with three existing rules: sealing refuses links
(SNAPSHOTS 4.3), verify counts a link at an entry path as `missing` (SNAPSHOTS 7, implementation points), and exFAT
cannot hold links. It would also break "readable without stradler" for a copy of the folder. Hard links from a local
store on the same file system are regular files to both rules and could save space, but they share bytes and
permissions with the store, so a change in one is a change in both; they are not part of v1 (section 18).

## 16. Comparison with git-annex and DataLad: why not wrap them

| Point | git-annex | DataLad | stradler (this design) |
|---|---|---|---|
| Identity | Per-file keys (backend such as SHA256E), not a dataset digest | git commit plus annex keys | Snapshot content digest; run records already pin it |
| Absent content | Symlink to `.git/annex/objects`, or pointer files in unlocked mode | Same as git-annex | Nothing in the folder |
| Where copies are | git-annex branch, merged between clones | Same | Reference document per snapshot plus append-only records |
| Safety on drop | `numcopies`, with live lock of remote copies | Same | `min_copies` per file on locations with a recent verified match |
| Transfer back ends | Special remotes (external protocol) | Same plus extensions | Store add-ons (JSON over standard input and output) |
| Requirements | git and git-annex binaries on every machine, a git repository per dataset | Python plus git plus git-annex, nested repositories | The stradler package only |
| exFAT and cold disks | Needs adjusted unlocked branches on such file systems | Same | Plain dataset tree |

Why not wrap: the snapshot digest is stradler's identity and is already in run records and pins; an annex layer would
add a second identity per file and a second record of locations (the annex branch) that must be kept consistent with
the snapshot. Its default link tree conflicts with SNAPSHOTS 4.3 and with exFAT targets. It adds a git repository and
binaries to every compute node and every cold-storage workflow. What is borrowed: the `numcopies` rule, `whereis`
(here `status` and `history`), `fsck` against remotes (here `probe`), and the special-remote idea (here store add-ons).
A store add-on that reads from an existing git-annex special remote or a DataLad dataset is possible and stays
outside the core.

## 17. Format changes, all additive

| Item | Change | Unchanged |
|---|---|---|
| `structure@1.2.0` | Area key `content` (section 3.1). | `structure@1.0.0`, `@1.1.0`. |
| `config@1.2.0` | `[stores.<name>] sidecar` (later phase). | `config@1.0.0`, `@1.1.0`; `[true_sources]` and `[replicas]` keep their meaning. |
| `local.toml` | Optional `[fetch] prefer = [location ids or store names]`. | Existing keys. |
| Reference document | New, `reference_version` 1 (section 4.2). | Snapshot documents (no key added). |
| Content record | New, `content_record_version` 1 (section 12). | Check records. |
| Status, get, drop, probe output | New versioned documents. | `verify` output. |
| `describe_version` 3 | Area key `content`. | Version 2 readers refuse 3, as its rule says. |
| Store protocol 1.1 | `capabilities`, `list`, `get`, later `put`; codes `no_space`, `unreachable`. | Protocol 1.0 and `digest`. |
| Workflow recipe | Optional key `subset` on `inputs` and step `in` entries. | Recipes without it. |

## 18. Out of scope

Deleting content from a location (remote drop) and store garbage collection; automatic replication to reach
`min_copies` (beyond the later `put`); automatic get after a probe (kept open, section 8); declaring content whose
digests are not known yet; version 1 snapshots; areas of mutability `immutable` or `mutable` (they have no snapshots
today; a method question below); link trees, hard links and copy-on-write clones; byte-range or partial-file fetch;
encryption and credentials (they stay in add-ons); bandwidth scheduling and parallel transfer inside one get (several
gets can run as scheduler jobs, one dataset each); syncing presence between machines; a background daemon.

## 19. Method questions, steps, dependencies and risks

### 19.1 Method questions (to settle while specifying, not Director decisions)

1. Can an `immutable` area be sealed or expected, or does on-demand stay limited to `sealed`?
2. Exact glob dialect shared with the default handler (WORKFLOWS 3.4), including `**`.
3. The freshness limit's default and where it is configured (`config@1.2.0` per area, or a CLI default only).
4. How `get` reports a location whose `capabilities` lack `get` but have `digest` (probe works, fetch does not).
5. Whether `history` lists versions whose snapshot document is missing locally (a `previous` that names nothing).
6. Whether the reference of a sealed (not expected) dataset is created by `seal` itself or on first use.

### 19.2 Steps in order (role, size)

| # | Step | Role | Size | Depends on |
|---|---|---|---|---|
| 1 | Normative spec `docs/CONTENT.md` from this note after the decisions; glossary terms; schemas `structure@1.2.0`, `config@1.2.0`, reference and content record JSON Schemas; `validate` rules | doc writer and Python writer | M | Decisions |
| 2 | Shared verified-copy routine (staging, hash while writing, fsync, rename, folder-bit journal, dataset lock); adopt copy mode moved onto it | Python writer | M | 1 |
| 3 | `stradler snapshot` and `stradler expect`; reference documents and `stradler reference` | Python writer | M | 1 |
| 4 | Selection resolver (paths, globs, subsets, size limits) and `stradler status` with states and depths | Python writer | M | 3 |
| 5 | Content records; `stradler probe` for local locations and through `digest` | Python writer | M | 3 |
| 6 | `stradler get` from local locations, incremental, dry run, outcomes, exit codes | Python writer | M | 2, 4, 5 |
| 7 | Store protocol 1.1 (`capabilities`, `list`, `get`) in the core and a reference test add-on | Python writer | M | 6 |
| 8 | `stradler drop` with the per-file copy rule | Python writer | M | 5, 6 |
| 9 | `stradler history` and `get --snapshot --to` | Python writer | S | 6 |
| 10 | Seal refusal on partial copies, `verify --present`, `verify --replicas` updating `last`, `describe_version` 3 | Python writer | S | 3, 5 |
| 11 | Workflow gate `content.absent`, recipe key `subset`, dropped-output rule, `fetch_inputs` if chosen | Python writer | M | 4, 6 |
| 12 | Fault-injection tests: interrupted fetch at each step, disk full on a small file system, mismatch, unreachable, lock races, a permission-less file system | tester | L | 6, 7, 8 |
| 13 | Later phase: `put`, sidecars, `expect --from-location` | Python writer | M | 7, Decisions 11 |
| 14 | README and command help | doc writer | S | 6 to 11 |

All test suites run as scheduler jobs, not interactively.

### 19.3 Risks

1. Sealing a partial working copy writes a shorter head (section 14 refuses it; the test of step 12 must cover it).
2. A data handler that lists files on disk silently sees fewer units in a partial copy (the workflow gate exists for
   this; it must cover `fixed` and `named` inputs too).
3. Drop relies on recorded matches; a location that loses content after its last probe gives a false count (bounded by
   the freshness limit and by `--reprobe`; no live lock exists as in git-annex).
4. Hashing cost: drop and `--verify` read every byte locally; probe of a local location reads every byte there. Large
   datasets need these as scheduler jobs, one dataset per task.
5. Lock files on network file systems: exclusive create is reliable on most, not all; the host check limits damage.
6. Read-only folder bits are flipped during get and drop; the journal must be written first, as in seal.
7. Reference documents and content records add files to the record area on every operation; projects that keep the
   record area in git see more commits (records hold no paths, so they are small).
8. A plain store holds one version: history beyond unchanged files depends on stores that keep versions.
9. Case-insensitive targets cannot hold some snapshots; `put` refuses rather than corrupting.
10. A `last` summary that disagrees with records after a crash; records win, and `--repair` restores it.

## 20. Decisions for the Director

Each decision lists the options and the recommendation. All recommendations are reversible until the spec of step 1
is released.

1. **How an area declares on-demand content.** (a) Area key `content = "on_demand"` in `structure@1.2.0`; (b) implicit
   for every sealed area with a true source or replicas; (c) a flag in `[true_sources.<area>]`. Recommendation: (a);
   it is explicit, per area, and lets `validate` and the workflow gate know where absence is allowed.
2. **Granularity of reference documents.** (a) One per snapshot, `<record>/references/<hex>.json`, carried forward to
   the next version; (b) one per dataset listing all versions. Recommendation: (a); its identity is fixed and it
   never mixes versions.
3. **Relation to `[true_sources]` and `[replicas]`.** (a) Keep separate: config is policy, references are facts, store
   locations are derived from config when a reference is created; (b) merge: replace both tables by references; (c)
   derive only: no reference file for store locations. Recommendation: (a); existing formats and `verify --replicas`
   stay as they are, and non-store locations (partner, archive, acquisition machine) have a place.
4. **Where the last result per location lives.** (a) A `last` summary in the reference document, rewritten from the
   records, records authoritative; (b) only in records, computed on read. Recommendation: (a), as the direction
   asks, with `--repair` to rebuild it.
5. **Per-machine presence cache.** (a) None in v1: presence is the file system, hashing on demand; (b) a git-ignored
   hash cache per machine. Recommendation: (a); modification times are not reliable on exFAT and a cache can be added
   later without a format change.
6. **Location rule for get.** (a) Only locations whose last probe was a full match; otherwise re-probe the missing
   entries automatically (`--no-probe` refuses); (b) refuse unless the last probe matched; (c) try any location and
   rely on the sha256 check. Recommendation: (a); it follows the direction (fetch only from a matched location) without
   making the user run probe by hand.
7. **Freshness of a match counted by drop.** (a) A recorded match no older than a limit (default 30 days), else a
   probe in the run; (b) any recorded match; (c) always a live probe. Recommendation: (a).
8. **`min_copies` when the area declares none.** (a) 1, as git-annex's default; (b) refuse drop until it is declared;
   (c) 2. Recommendation: (b) for a research archive, because a drop is the only step here that can lose the last
   local copy; (a) is the alternative if declaring replicas for every on-demand area is too strict.
9. **Older versions.** (a) Only into a separate folder with `get --snapshot --to DIR`; (b) also replace the working
   copy with an older version. Recommendation: (a); the dataset folder always belongs to the head.
10. **URL and archive locations.** (a) Recorded in the reference and transferred only through an add-on that
    resolves them; (b) the core speaks HTTP itself. Recommendation: (a); the core stays free of network code and
    credentials.
11. **Scope of the first release.** (a) Steps 1 to 12 and 14 (declare, probe, get, drop, status, history; no `put`,
    no sidecars, no `expect --from-location`); (b) include step 13 as well. Recommendation: (a); copies into stores
    are made with existing tools meanwhile and checked by `probe`.
12. **Store layouts that keep every version.** (a) Plain layout only now; history is what locations still hold, plus
    add-on stores with their own versions; (b) add a plain per-version folder layout; (c) add an optional
    content-addressed layout for non-cold stores. Recommendation: (a) now, (c) later as an option, never on cold disks.
13. **Staging location.** (a) A hidden sibling of the dataset folder, refusing a cross-device rename; (b) a staging
    file beside each final file, inside the dataset folder. Recommendation: (a); nothing ever appears in the dataset
    folder that is not a verified file.
14. **Workflow inputs absent at plan time.** (a) Refuse with the exact get command; (b) fetch automatically; (c) refuse
    by default, fetch with the run option `fetch_inputs`. Recommendation: (c).
15. **Automatic get after a first successful probe.** (a) Manual in v1, with the `watch` object reserved for a later
    `reference_version`; (b) build it now. Recommendation: (a), as the direction allows.

## Appendix: requirement coverage

| Requirement | Section |
|---|---|
| Wanted 1: declare content without holding it; fetch all, one dataset, chosen paths, from true source or replica | 3, 6, 9 |
| Wanted 2: drop with `min_copies` verified copies elsewhere | 10, Decisions 7, 8 |
| Wanted 3: history via `previous` | 11, Decision 12 |
| Real files, links and SNAPSHOTS 4.3 | 3.3, 15 |
| Readable without stradler; exFAT cold storage | 13.1 |
| sha256 on every fetch; temp plus rename | 9.2, 9.3 |
| Local and add-on stores; additive protocol operations | 13 |
| Additive format rules | 17 |
| CLI commands, arguments, exit codes, output | 5.3, 7 to 11 |
| Where presence is recorded; git | 4.5, 5.2 |
| Seal, verify, replicas, workflows | 14 |
| Error cases (unreachable, mismatch, disk full, interrupted) | 13.3 |
| Out of scope | 18 |
| git-annex and DataLad | 16 |
| Decisions | 20 |
| Addendum 1: reference documents, locations, no credentials, records, drop rule, relation to config, git, link rot | 4, 10, 12 |
| Addendum 2: no link look, expected snapshot, states, probe, get only from matched locations, automatic get later | 3, 5, 8, 9.2, Decision 15 |
| Addendum 3: selections, subsets, limits, dry run, partial state, records with selection, incremental, drop by selection, workflow subsets, batches of 500 | 6, 5, 9, 10, 12, 13.2, 14 |
