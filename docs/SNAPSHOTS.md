# stradler snapshot format

Status: draft (2026-11-15). Terms are defined in `GLOSSARY.md`; terms defined in section 1 of this document are proposed
for addition to the glossary (section 10). Section numbers in parentheses such as "WORKFLOWS 10.1" refer to
`WORKFLOWS.md`. Field layouts of the project files are in `SCHEMAS.md`.

This document is the normative, versioned public format of a stradler snapshot. It is written so that any
implementation (the Python package, the Rust crate, or an external tool) can recompute a snapshot digest from this
document alone, without reading any stradler source code.

Each rule carries its origin:

- **design**: stated by the stradler design;
- **existing**: what the current code writes (section 2 only; a description of the legacy format, not a design
  statement);
- **proposed**: needed to make the rule implementable; the design leaves it open. Proposed items are collected in
  section 10. Rules in sections 3 to 9 are proposed unless marked design or existing.

The canonical JSON (WORKFLOWS 10.1) and the digest text form (WORKFLOWS 10.2) are reused unchanged. Every digest
below is `sha256:` followed by 64 lowercase hexadecimal digits, computed as stated in WORKFLOWS 10.2. The existing digests
(`plan_digest`, `content_digest`, `inputs_digest`, `file_digest`, and the task input digest of WORKFLOWS 10.3) are not
changed by this document.

Use cases (design):

- sealing a dataset in a sealed area (section 2 for version 1; section 3 for version 2);
- checking a sealed dataset, or a copy of it in another store, against its snapshot (section 7);
- referring to a dataset version by its digest in run records and pins (section 6.4).

## 1. Terms

| Term | Meaning | Origin |
|---|---|---|
| snapshot | An immutable, digest-identified version of a dataset (GLOSSARY). A snapshot is identified by its content digest and is described by one snapshot document. | design |
| snapshot document | The JSON file that describes one snapshot: its dataset, its layout, its content digest and the entries (or the root of the tree) that the digest covers. Its own bytes are not digested. | proposed |
| content digest | The digest of the content of a snapshot, computed as stated for its format version. It is the snapshot id: two documents with the same content digest describe the same content. | proposed |
| entry | One record that describes one regular file of a snapshot: its path, digest and size (layout `flat`), or one child record of a directory node (layout `tree`). | proposed |
| layout | The arrangement of the entries of a version 2 snapshot: `flat` (one sorted list) or `tree` (one node per directory, each a sorted list of child records). | proposed |
| dataset | A versioned collection of files inside an area (GLOSSARY). In this document the dataset is the folder given by its project-relative path. | design |
| record area | The area of the project with role `record` in `structure.toml`; if there is none, the folder `records` (the default in the code). | existing |

## 2. Format version 1 (legacy)

Version 1 is what `export_sealed` in `src/stradler/workflow/export.py` writes today. It is kept so that existing
snapshots stay readable. A version 1 document has no `snapshot_version` key.

### 2.1 Storage and document

The document is stored at `<project root>/<record area>/snapshots/<hex>.json`, where `<hex>` is the content digest
without the `sha256:` prefix. The file is written to a temporary name and renamed into place (section 6.2). Its bytes
are indented JSON with sorted keys and a final LF; the bytes are not canonical, and the digest never depends on them.

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `dataset` | string, project-relative folder | yes | existing | The dataset folder, without leading or trailing `/`. |
| `files` | array of entry objects (section 2.2) | yes | existing | One entry per regular file, sorted by path (section 2.3). |
| `digest` | digest | yes | existing | The content digest: the digest of the canonical JSON of `files` (section 2.3). |

### 2.2 Entry

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `path` | string | yes | existing | Path of the file relative to the dataset folder, `/` as separator. |
| `digest` | digest | yes | existing | SHA-256 of the bytes of the file. |
| `size` | integer, at least 0 | yes | existing | Size of the file in bytes. |

### 2.3 Digest

The content digest of version 1 is the digest of the canonical JSON of the array `files` (WORKFLOWS 10.1, 10.2):
the object members of each entry are written in the order `digest`, `path`, `size`, and the entries are in the order of
section 2.4. The `dataset` key and the document bytes are not digested. Version 1 has no algorithm key: its algorithm is
SHA-256, the only algorithm the code uses.

### 2.4 Which files are recorded

The writer walks the dataset folder with `os.walk` and records the following (existing):

- every name that `os.walk` lists as a file, including hidden files;
- a symbolic link whose target is a regular file is recorded with the content of the target (the file is opened, which
  follows the link); its path is the path of the link;
- a symbolic link to a directory is not descended into and is not recorded, without an error;
- a broken symbolic link makes the export fail with an operating-system error;
- a directory that cannot be listed is skipped without an error (`os.walk` is called without an error handler);
- empty directories are not recorded.

The entries are sorted by the UTF-8 bytes of `path`. A name that cannot be encoded as UTF-8 makes the writer fail at the
sort.

### 2.5 Sealing behaviour (existing)

The writer refuses, with an error and without writing a document, when:

- the dataset folder does not exist;
- the dataset is not in an area of mutability `sealed`;
- the dataset has no files;
- the record area already holds a version 1 document of the same dataset with a different digest (a changed dataset
  becomes a new versioned dataset).

After the document is written, the writer removes the write permission bits of every file and directory of the dataset.
Permission bits are not part of any digest.

### 2.6 Example

For the files `a.txt` (bytes `hello` followed by LF, 6 bytes) and `sub/b.bin` (bytes `x`, 1 byte), the entries are
`[{"digest":"sha256:5891...6be03","path":"a.txt","size":6},{"digest":"sha256:2d71...4881","path":"sub/b.bin","size":1}]`
(digests shortened here; the full values are in section 5.4), and the version 1 content digest is
`sha256:5d10b0c2f98dac43fd194754aaa76463d4a89a58827fe7915702d3cde0c659ca`.

## 3. Format version 2

Version 2 is the format of new snapshots. The document has `snapshot_version` 2. Its content digest is taken over
the algorithm, the layout and the entries (or the root of the tree), and never over the metadata keys.

### 3.1 Document

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `snapshot_version` | integer, constant `2` | yes | proposed | Version of this format (section 8). |
| `algorithm` | string, constant `"sha256"` | yes | proposed | Hash algorithm of the file digests and of the content digest. The only value in this version. |
| `layout` | enum: `flat`, `tree` | yes | proposed | Layout of the entries (section 5). |
| `dataset` | string, project-relative folder | yes | proposed | The dataset folder, without leading or trailing `/`. |
| `digest` | digest | yes | proposed | The content digest (section 3.2): the snapshot id. |
| `entries` | array of entry objects | exactly when `layout` is `flat` and the entries are stored inline | proposed | The entries, sorted (section 4.3). Excludes `entries_stream` and `root`. |
| `entries_stream` | string, file name | exactly when `layout` is `flat` and the entries are stored as a stream | proposed | File name, in the same folder, of the entry stream (section 5.3). Excludes `entries`. |
| `root` | digest | exactly when `layout` is `tree` | proposed | Digest of the root node (section 5.2). |
| `file_count` | integer, at least 1 | yes | proposed | Number of entries. Readers check it against the entries. Not digested. |
| `total_size` | integer, at least 0 | yes | proposed | Sum of the sizes of all entries, in bytes. Readers check it. Not digested. |
| `created` | timestamp, UTC | yes | proposed | Time at which the document was written. Not digested. |
| `previous` | digest or `null` | yes | proposed | Content digest of the earlier version of the same dataset, or `null` for the first version (section 3.4). Not digested. |
| `grade` | string | no | proposed | The grade of the content (`SCHEMAS.md` section 5.1: the widest audience the content may be disclosed to). The value must be no less strict than the grade of the area. This version stores it and does not interpret it. Not digested. |
| `provenance` | object | no | proposed | Origin metadata, stored and never interpreted (section 3.5). Not digested. |

A reader refuses a version 2 document that has a key not listed here (proposed; the convention of `SCHEMAS.md` section 1).
The timestamp form is `YYYY-MM-DDTHH:MM:SSZ` (WORKFLOWS section 1).

### 3.2 Content digest

The content digest is the digest of the canonical JSON of a content object (WORKFLOWS 10.1, 10.2):

- layout `flat`: the object `{"algorithm": "sha256", "entries": [...], "layout": "flat"}`, where the array is the
  sorted list of the entries (section 4.2);
- layout `tree`: the object `{"algorithm": "sha256", "layout": "tree", "root": "<root node digest>"}` (section 5.2).

The members of the object are sorted by the canonical rule; no other member is part of the object. The content digest therefore covers the algorithm, the layout and the entries, and never `created`, `previous`,
`grade`, `provenance`, `dataset`, `file_count` or `total_size`. The same content always has the same content digest, in
any store and at any time.

Because the content digest is derived from the algorithm and the layout as well, a version 2 content digest is not the
version 1 digest of the same files (section 2.6 and section 5.4).

### 3.3 Entry

For layout `flat` an entry is an object with the members below. For layout `tree` the same information is held by the
child records of section 5.2.

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `path` | string, relative path | yes | proposed | Path relative to the dataset folder, `/` as separator (section 4.1). |
| `digest` | digest | yes | proposed | SHA-256 of the bytes of the file (section 4.4). |
| `size` | integer, 0 to 2^53 | yes | proposed | Size of the file in bytes. |

### 3.4 Lineage

`previous` links a version to the earlier version of the same dataset, so that the history of a dataset can be read from
its documents (proposed):

- `previous` is `null` for the first version of a dataset;
- otherwise it is the content digest of a version 2 document of the same `dataset`, the head of that dataset's lineage;
- a head is a version 2 document of the dataset that no other version 2 document of the dataset names in `previous`;
  a writer refuses to write a version whose `previous` is not the head, and refuses when the dataset would get two heads.

Lineage is not checked across stores, and it is not part of any content digest. A version 1 snapshot is not a
`previous` (section 10).

### 3.5 Provenance

`provenance` is an object with optional members (proposed). Stradler stores it and never interprets it:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `origin` | string, opaque identifier | no | proposed | Identifier of the origin of the content, as the writer gives it. Stradler does not resolve it. |
| `checked_at` | timestamp, UTC | no | proposed | Time at which the origin was last checked. |
| `metadata` | object | no | proposed | Free metadata; any canonical JSON object (WORKFLOWS 10.1). |

## 4. Entries and paths

### 4.1 Paths

- A path is relative to the dataset folder. Its segments are separated by `/` (U+002F). It does not start with `/`.
- Each segment is a non-empty UTF-8 name that is not `.` or `..` and does not contain `/`. A path has at least one
  segment.
- Names are not normalized: no Unicode normalization, no case folding, no trimming. Two names that differ only in their
  normalization form are two different names.
- A name that is not valid UTF-8 is refused at sealing (proposed: the writer stops with an error and writes no document).
  A reader meets such a name only in a store and reports it as `unreadable` (section 7.2).
- Names are written in the canonical JSON as UTF-8 (WORKFLOWS 10.1). No escape is applied beyond that rule.

### 4.2 Sort order

Entries are sorted by the UTF-8 bytes of their full `path` for layout `flat`. For a tree node, child records are sorted by
the UTF-8 bytes of `name`. Byte order and code point order agree for UTF-8, but the rule is stated on bytes so that
an implementation with a different string type gives the same order. No two entries or children have the same path or
name.

### 4.3 What is recorded

- Regular files only. Every regular file under the dataset folder, at any depth, is recorded, including hidden files.
  No exclusion list exists in version 2.
- Empty directories are not recorded. A directory that holds no regular file at any depth has no entry and no node.
- Symbolic links are refused at sealing (proposed; the rule is chosen here, see the reasons below). A symbolic link to a
  file or to a directory, and a broken link, makes the writer refuse. Any other non-regular entry (device, socket, pipe)
  is refused in the same way.
- Permission bits, owner, and modification or access times are not recorded and are not digested.

The reason for refusing links: with links recorded, the content digest would depend on whether a copy in another store
preserves them, and a link can point outside the dataset. With links refused, the digest depends only on the names and
the bytes of the regular files. Version 1 keeps its own rule (section 2.4) because its documents exist.

### 4.4 File digest and size

The digest of a file is the SHA-256 of its bytes, in the form of WORKFLOWS 10.2 (`sha256:` plus 64 hexadecimal digits).
The size is the number of bytes. Both are taken from the bytes read by the writer; a file whose bytes change during
sealing makes the writer fail.

The one exception is a seal run with `--reuse-verified` (section 7.5, ADOPT.md "Verified digests"): a file whose size
and modification time match its machine-local cache line takes its digest from that line and is not read by the
writer, so a change that keeps both size and mtime is not seen by the writer. The document format does not change, and
the final verify of the seal reads every file (section 7.5, step 5). This is an implementation choice, not part of
the digest rules above.

## 5. Layouts

Two layouts exist for version 2. A writer chooses one per snapshot (the choice rule is not fixed; section 10). The
content digest is defined for each layout in section 3.2.

### 5.1 Layout `flat`

- The document holds the sorted list of entries (section 4.3) either inline, in `entries`, or as a stream, in
  `entries_stream` (section 5.3).
- The content digest is the digest of `{"algorithm":"sha256","entries":[...],"layout":"flat"}` in canonical JSON.
- Use: small datasets, where a reader needs the whole list in one file.

### 5.2 Layout `tree`

Every directory that holds at least one regular file, at any depth, is a node. The root node is the dataset folder.

- A node is the sorted array of its child records. A child record is an object with four members:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `name` | string, one segment (section 4.1) | yes | proposed | Name of the child in its directory. |
| `kind` | enum: `file`, `dir` | yes | proposed | `file` for a regular file, `dir` for a directory node. |
| `digest` | digest | yes | proposed | For `file`, the SHA-256 of the file bytes; for `dir`, the node digest of the child directory. |
| `size` | integer, 0 to 2^53 | yes | proposed | For `file`, the size of the file; for `dir`, the sum of the sizes of all files beneath it. |

- The node digest is the digest of the canonical JSON of the array of child records of that node (WORKFLOWS 10.1, 10.2),
  children sorted by `name` (section 4.2). A node is never empty.
- The root node digest is the node digest of the dataset folder. It is stored as `root` in the document.
- The content digest is the digest of `{"algorithm":"sha256","layout":"tree","root":"<root node digest>"}` in canonical
  JSON. (This is the wrapper of section 3.2: the root node digest, with the algorithm and the layout added so that they
  are covered.)
- Nodes are stored one file per node (section 6.3). A node file holds exactly the canonical JSON of its child array, so
  that the SHA-256 of the file bytes is the node digest.
- A subtree is checked alone by recomputing the node digest of its directory (section 7.3).

### 5.3 Entry stream (layout `flat` only)

For a very large flat snapshot, the entries may be stored as a JSON Lines stream instead of inline (proposed):

- The stream file is UTF-8 without a byte-order mark. It holds one entry per line; each line is the canonical JSON of one
  entry (section 3.3) followed by LF. Lines are in the sort order of section 4.2. A stream has at least one line.
- The stream file is named `<hex>.entries.jsonl`, where `<hex>` is the content digest without its prefix, and is stored
  next to the document (section 6.3).
- The content digest of a stream equals the content digest of the inline form. It is computed from the stream without
  holding it in memory: start the SHA-256 with the bytes `{"algorithm":"sha256","entries":[`; then, for each line in order,
  its bytes without the LF, with a `,` byte before every line except the first; then the bytes `],"layout":"flat"}`.
  The bytes hashed are exactly the canonical JSON of the content object of section 3.2, so the digest is the same as
  for the inline form.
- The document then holds `entries_stream` and not `entries`. `file_count` is the number of lines.

### 5.4 Worked example (not conformance data)

Two files: `a.txt` with the bytes `hello` and LF (6 bytes), and `sub/b.bin` with the byte `x` (1 byte). The values were
computed with the rules of this section.

| Quantity | Value |
|---|---|
| file digest of `a.txt` | `sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03` |
| file digest of `sub/b.bin` | `sha256:2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881` |
| version 1 content digest | `sha256:5d10b0c2f98dac43fd194754aaa76463d4a89a58827fe7915702d3cde0c659ca` |
| version 2 flat content digest (inline or stream) | `sha256:8d49f9e2ac5d0091f4191f6acb630afb794cb14ac7388e48f150a2a3d9791288` |
| version 2 tree node digest of `sub` | `sha256:1b481426fe28c8e29ea5d61630cf07b25b113788016cc0dda4927cec85056213` |
| version 2 tree root node digest | `sha256:716f5a1fe2277248e4ca3b3da8af3f74fd8db3cb09c50fd5b1b74cc76c5acb3e` |
| version 2 tree content digest | `sha256:fbdc140cfca370afc606e66f6feb4401fa406def1dae2a9bfa0bd3192424c6b3` |

The flat content object is (one line):
`{"algorithm":"sha256","entries":[{"digest":"sha256:5891b5b5...6be03","path":"a.txt","size":6},{"digest":"sha256:2d711642...4881","path":"sub/b.bin","size":1}],"layout":"flat"}`
(digests shortened in this line only).

The node `sub` is the child array `[{"digest":"sha256:2d711642...4881","kind":"file","name":"b.bin","size":1}]`. The root
child array is `[{"digest":"sha256:5891b5b5...6be03","kind":"file","name":"a.txt","size":6},{"digest":"sha256:1b481426...5213","kind":"dir","name":"sub","size":1}]`.

## 6. Storage

### 6.1 Folders

All paths are relative to the project root. `<record>` is the record area (section 1).

| Item | Location | Origin |
|---|---|---|
| version 1 document | `<record>/snapshots/<hex>.json` | existing |
| version 2 document | `<record>/snapshots/v2/<hex>.json` | proposed |
| version 2 tree node | `<record>/snapshots/v2/nodes/<hex>.json` | proposed |
| version 2 entry stream | `<record>/snapshots/v2/<hex>.entries.jsonl` | proposed |
| check record | `<record>/checks/<name>.json` (section 7.4) | proposed |

The version 2 folders are separate from `snapshots/`, so that the version 1 writer, which reads every `.json` file
directly in `snapshots/`, does not read version 2 documents.

### 6.2 Writing

- A file is written to a temporary name in its folder, `<name>.tmp-<pid>`, and then renamed onto its final name with an
  atomic replace. A reader never sees a partly written file under the final name. This is the rule of
  `write_json_atomic` in the code.
- The writer writes the nodes and the entry stream first, then the document. A document never refers to a file that does
  not exist yet.
- A version 2 file is named by the digest it is about (section 6.3). If a file of that name already exists, the writer
  keeps it and does not replace it. The `created` value of the first document of a content therefore stays.
- Documents, nodes and streams are never edited in place.

### 6.3 File naming

- A version 2 document is named by its content digest: `<hex>.json`.
- A tree node is named by its node digest: `nodes/<hex>.json`. Its bytes are the canonical JSON of its child array, with
  no trailing LF.
- An entry stream is named by its content digest: `<hex>.entries.jsonl`.
- `<hex>` is the 64 lowercase hexadecimal digits of the digest, without the `sha256:` prefix.

### 6.4 References by digest

Run records and pins refer to a dataset version by its content digest (GLOSSARY: pin, run record). A reference that a
reader must recompute also carries `snapshot_version` (1 or 2), because a version 1 digest and a version 2 digest of the
same files differ, and a bare digest does not say which format it belongs to (proposed). A reader finds a document by
its digest in the folder of its version (section 6.1).

## 7. Checking a snapshot

This section defines the behaviour of `stradler verify` (`stradler.verify`). Sealing, which runs verify at its end,
is described in section 7.5.

Points of this section that the implementation fixes (previously proposed):

- The dataset folder in a store is `<store path>/<dataset>`, where `<store path>` is `[stores.<name>] path` of
  `.stradler/local.toml` and `<dataset>` is the `dataset` of the document. A store path that is not a directory is
  `unreachable`; a reachable store without the dataset folder gives every entry as `missing`.
- A check without `--store` reads the dataset folder in the project and its record is named with the store `local`.
- Without a store name the target of a check is the dataset folder (its head, section 3.4) or a snapshot digest.
- A symbolic link at a path of a version 2 snapshot is `missing` (no regular file lies there); at any other path it is
  `added`.
- An error while listing a directory of the store (not only the store path) makes the result `unreachable`.
- Exit status: 0 `match`, 1 `differs`, 2 `unreachable`, 3 error (no valid snapshot, a document that does not reproduce its
  digest, an unknown store name, a usage error). An error writes no check record.

### 7.1 Inputs and first steps

The check takes a snapshot document (version 1 or 2), a store name from `[stores]` in `config.toml`, and the dataset
folder in that store.

1. Read the document. For version 2, recompute the content digest from the entries, the stream or the tree, and compare
   `file_count` and `total_size`. A document that does not reproduce its own digest is refused: the check stops with an
   error, and no check record is written (the document is not valid evidence).
2. Reach the store and the dataset folder. If the store cannot be reached, the result is `unreachable` (section 7.4).

### 7.2 Comparison and categories

The check lists the regular files of the dataset folder in the store, at any depth, and compares them with the entries
of the snapshot by path. Each path falls in exactly one category:

| Category | Condition |
|---|---|
| `match` | The path is in the snapshot, the file is readable, and its size and digest equal the entry. |
| `missing` | The path is in the snapshot and no regular file lies at it in the store. |
| `added` | A regular file lies at a path that is not in the snapshot. A symbolic link or other non-regular entry at a path that is not in the snapshot is also `added`. |
| `changed` | The path is in the snapshot, a readable regular file lies at it, and its size or digest differs. |
| `unreadable` | The file lies at a path of the snapshot or of the store and cannot be read to its end, or its name is not valid UTF-8 and so cannot be matched. Takes precedence over `changed`. |

For a version 1 snapshot the check follows symbolic links to regular files and ignores symbolic links to directories, as
the version 1 writer does (section 2.4). For a version 2 snapshot, any symbolic link in the store is `added` (section 4.3).

The check never writes to the store. It opens files for reading only, does not change permission bits and does not touch
modification times.

### 7.3 Checking one subtree

For a tree-layout snapshot, a check may cover one subtree: the directory is named by its path, the check recomputes its
node digest from the store, and compares it with the node digest of that directory in the snapshot (section 5.2). The
result is `match` when the two are equal. The same categories apply to the files of that subtree.

### 7.4 Result and check record

Result values (proposed):

| Result | Meaning |
|---|---|
| `match` | Every category except `match` has a count of 0. |
| `differs` | At least one of `missing`, `added`, `changed` or `unreadable` is not 0. |
| `unreachable` | The store could not be reached before the comparison began, or it became unreachable during it. No counts are recorded, because the comparison is incomplete. |

A check writes one record in `<record>/checks/`, named `<UTC time as YYYYMMDDTHHMMSSZ>-<store>-<hex>.json`. If the name
exists, the writer adds a numeric suffix; a record is never overwritten (proposed). The record holds only these members
(proposed):

| Key | Type | Required | Meaning |
|---|---|---|---|
| `time` | timestamp, UTC | yes | Time at which the check finished. |
| `store` | string, name (`^[A-Za-z0-9_-]+$`) | yes | Logical name of the store, from `[stores]`. |
| `snapshot` | digest | yes | Content digest of the snapshot. |
| `snapshot_version` | integer, 1 or 2 | yes | Format version of the snapshot. |
| `result` | enum: `match`, `differs`, `unreachable` | yes | Result (table above). |
| `counts` | object: `match`, `missing`, `added`, `changed`, `unreadable`, each an integer | yes, unless `result` is `unreachable` | Number of paths in each category. |

The summary record holds no path, no file name and no file content. The per-path list of a check is shown to the caller
and not stored here (section 10).

### 7.6 Checking the declared copies of an area

`stradler verify --replicas <area>` checks the copies that `[replicas.<area>]` of `config@1.1.0` declares
(`SCHEMAS.md` section 4.1). For each dataset of the area that has a version 2 snapshot, it takes the latest snapshot
(the head, section 3.4; a dataset with two heads is reported as a problem and not checked) and, for each declared
store, runs the check of sections 7.1 to 7.4 and writes one check record in `<record>/checks/`. One record is written
per (store, snapshot). The command takes no target, `--store` or `--subtree`.

- A store with a `path` in `local.toml` is a local store: the core reads it as in section 7.2.
- Any other store is checked through the `digest` operation of its store add-on (`ADDONS.md` section 8): the core
  sends the paths of the snapshot entries and compares the size and sha256 returned with the entries, so nothing is
  downloaded. A file the add-on does not return is `missing`; a file with another size or digest is `changed`. Files
  that the snapshot does not list cannot be seen this way, so `added` and `unreadable` are 0 for such a store.
- A store that cannot be reached (an offline local store, or an add-on error other than `unsupported_operation`)
  gives the result `unreachable` and a record without counts.
- An add-on that lacks the `digest` operation, or a store without a usable add-on, is reported as
  `not_supported` with the message "verify not supported"; no record is written for it, because the check was not made.

Exit status: 1 if any check differs, else 2 if any store is unreachable, else 3 if any store is "verify not
supported", a dataset has no single head or the request is invalid, else 0.

`stradler describe --json` summarizes the recorded checks per (area, store) without checking anything (`DESCRIBE.md`):
the last check time and result for the area's latest snapshots, and whether `min_copies` is met by the declared stores
whose result is `match`.

## 8. Versioning

- `snapshot_version` is an integer. A document without the key is version 1 (section 2). Version 2 is the format of this
  section 3, with `snapshot_version` 2.
- A change of the document shape, of the entry shape, of the sort order, of the content object or of the digest
  procedure raises `snapshot_version`. A change of the value of `algorithm` raises `snapshot_version` as well.
- A reader refuses a document whose version it does not know. A version 2 reader refuses a version 2 document with a key not
  listed in section 3.1 (proposed).
- A version 2 reader reads version 1 documents. Version 1 documents are not rewritten, and no migration is defined: a
  version 1 snapshot keeps its digest and its place (section 2.1).
- Optional metadata keys (`grade`, `provenance`) are not digested, so that their values never change a snapshot id. Their
  meaning is defined where they are first used (section 3.1).

## 9. Conformance data layout

Conformance cases for snapshots are shared by all implementations. The data is written by a later work item; this section
fixes its layout only (proposed).

```
tests/conformance/snapshot/<case>/
  input.json       # the input tree, as a description
  expected.json    # the expected digests and counts
```

`input.json` is a canonical JSON object with the members:

| Key | Type | Meaning |
|---|---|---|
| `files` | array of objects `{path, encoding, content}`, sorted by `path` (section 4.2) | Regular files of the input tree. `encoding` is `utf-8` (the `content` is the text) or `base64` (the `content` is the standard Base64 of the bytes). |
| `empty_dirs` | array of paths, sorted | Directories with no regular file at any depth. They must not change any digest. |

A case contains no symbolic link. Its files must pass the rules of section 4.

`expected.json` is a canonical JSON object with the members:

| Key | Type | Meaning |
|---|---|---|
| `v1` | object `{digest, file_count, total_size}` | The version 1 content digest of the input tree (section 2), when the case is also a version 1 case. |
| `flat` | object `{digest, file_count, total_size}` | The version 2 content digest for layout `flat`. The inline and the stream forms must both give this digest. |
| `tree` | object `{digest, root, file_count, total_size}` | The version 2 content digest and root node digest for layout `tree`. |

A conformance run recomputes each value from `input.json` and compares it byte for byte with `expected.json`. Expected
values are never produced by the implementation under test alone; a second implementation must agree before a value is
accepted (proposed).

### 7.5 Sealing

`stradler seal <dataset> [--layout flat|tree] [--stream] [--grade G] [--provenance-file F] [--json]` (fixed by the
implementation; proposed before):

1. The dataset lies in an area of mutability `sealed`, else the seal is refused before anything is changed.
2. The content digest is computed and a journal is written to `<record>/snapshots/v2/pending/<sha256 of the dataset
   name>.json` (dataset, intended digest, layout, stream, grade, provenance and the original permission bits of every
   file and directory). No permission is changed before the journal exists.
3. Files and directories lose their write bits.
4. The version 2 document is written, with `previous` set to the current head of the dataset (section 3.4).
5. The journal is removed and verify runs on the dataset. Exit 0 only when the seal is complete and verify matches.

`stradler seal --reuse-verified` (default off) changes step 2 only: the file digests of the content digest and of the
document may be taken from the machine-local cache written by `adopt` (ADOPT.md, "Verified digests"), for each file
whose size and `mtime_ns` equal its latest cache line. The journal, the permission steps and the verify of step 5 are
unchanged, and step 5 reads every file. The seal result then carries `reused_digests` (the number of distinct files
whose digest came from the cache) and `cache_warnings` (the number of malformed cache lines read). These are result
keys of the command; they are not part of any document, digest or check record.

A rerun on a dataset with a journal recomputes the digest. If it equals the journal's, the seal finishes with the
journal's own options; if not, it is refused and names `stradler seal --rollback <dataset>`, which restores the
recorded permission bits and removes the journal and the document and entry stream of the intended digest (tree nodes
are content-addressed and are kept). Killing the process at any point therefore loses no data.

## 10. Points not fixed

These points are proposed in this document. Each can be changed before the format is released as version 2.

1. The choice of layout (`flat` or `tree`) for a snapshot, and the size at which a writer switches (section 5).
2. Whether the child array of a very wide tree node is also stored as a stream (section 5.2 stores it in one node file).
3. The rule for symbolic links: this document refuses them at sealing (section 4.3). The alternative, recording links as
   links, is not chosen; it would need a link record kind.
4. Whether a version 1 snapshot can be the `previous` of a version 2 document (section 3.4), and how a lineage is
   read across the two versions.
5. Resolved: the meaning of `grade` is defined in `SCHEMAS.md` section 5.1 (structure version 1.1.0). That the value is no
   less strict than the grade of the area (section 3.1) stays a rule of the snapshot writer.
6. The members of `provenance` beyond `origin`, `checked_at` and `metadata` (section 3.5).
7. Unicode normalization of names on stores that normalize names (for example, two names that a file system presents as one)
   (section 4.1). The current rule is no normalization.
8. Whether the per-path list of a check is stored, and where (section 7.4).
9. The writer's maximum number of entries for layout `flat` inline, and of children of one tree node.
10. Terms of section 1 (snapshot document, content digest, entry, layout) to be added to `GLOSSARY.md`.
