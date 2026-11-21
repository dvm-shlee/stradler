# Retention per version

Status: normative text for the first retention release. Items marked **(later release)** are specified here but are
not implemented in the first release.

Scope of this document: the retention policy document, where it lives, how its revisions form a lineage, its keys,
its command-line interface, and the rules that evaluate and guard versions. Version diff and the version-keeping store
are specified elsewhere and are not repeated here. Other section references ("SNAPSHOTS 3.4", "WORKFLOWS 10.1") point
into `docs/`.

## 1. Terms

| Term | Meaning |
|---|---|
| retention policy | A versioned JSON document for one dataset or one area that states which versions of a dataset's lineage must stay held (section 3). |
| scope | The dataset or area a policy applies to (section 2). |
| revision | One written policy document. Each change writes a new revision; revisions are never edited. |
| head | The newest revision of a scope: the revision that no other revision of the scope names in `previous` or `merges`. |
| kept version | A version that the applicable policies keep: the head, a pinned version, or a version within `keep_last` or `keep_days`. **(later release, evaluation)** |
| droppable version | A version of the lineage that no applicable policy keeps. It may still be held; nothing removes it automatically. **(later release, evaluation)** |
| held version | A version for which at least one location of its reference has a full match no older than the freshness limit, or a versioned store that holds it. The working copy never counts. **(later release, evaluation)** |
| lineage | The chain of snapshot versions of one dataset, linked by each version's `previous` digest. |

## 2. Scope

A policy applies to one dataset, or to every dataset of one area. Both kinds may exist at the same time.

- A version is kept when any applicable policy keeps it: the rules of the scopes combine as a **union**. A dataset
  policy can add pins and widen limits, but it can never weaken the rules of its area.
- The head of a dataset is always kept, with or without a policy.
- A dataset with no applicable policy keeps only its head. Nothing protects older versions until a policy names them.

### 2.1 Effective rules

`stradler retention show <dataset>` prints the effective rules, which combine the area policy and the dataset policy
of the dataset (the union of section 2):

- `keep_last` and `keep_days`: the larger of the two values; a `null` defers to the other policy.
- `hold_copies`: the larger of the two values (1 when no policy names it).
- `pins`: the union of both pin lists, one entry per snapshot digest.
- `policies`: the digests of the head revisions used.

This release only combines the stated rules. It evaluates no version (section 5).

## 3. Document (`retention_version` 1)

The policy is a record, not a path list. It names versions by content digest and never lists files.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `retention_version` | integer, constant `1` | yes | Version of this format. Rule key. |
| `scope` | object | yes | `{"kind": "dataset", "dataset": "<project-relative folder>"}` or `{"kind": "area", "area": "<area name>"}`. Rule key. |
| `keep_last` | integer, at least 1, or `null` | yes | Keep the newest N versions of the lineage, the head included. `null` means no count rule. Rule key. |
| `keep_days` | integer, at least 0, or `null` | yes | Keep a version until D days after it was superseded. The clock starts at the `created` time of the revision whose `previous` names the head. `null` means no time rule. Rule key. |
| `hold_copies` | integer, at least 1 | the writer always writes it; a document without it means 1 | How many held locations each kept version must have. Rule key. |
| `pins` | array of pin objects, sorted by `snapshot`, no snapshot twice | yes, may be empty | Versions kept regardless of the other rules. Must be empty when `scope.kind` is `area`. Rule key. |
| `previous` | digest or `null` | yes | Digest of the head revision of the same scope that this revision follows; `null` for the first revision. Digested. |
| `merges` | digest | only on a merge revision (`set --merge`) | Digest of the second head that a merge revision absorbs (section 4.3). Absent on every other revision. Digested. |
| `created` | timestamp, UTC | yes | When this revision was written. Not digested. |
| `rules_digest` | digest | yes | Digest of the canonical JSON (WORKFLOWS 10.1, 10.2) of the rule keys alone: the document without `digest`, `created`, `previous`, `merges` and `rules_digest`. Revisions with equal rules have equal `rules_digest`. |
| `digest` | digest | yes | Digest of the canonical JSON (WORKFLOWS 10.1, 10.2) of the document without `digest` and `created`. It is the identity of the revision and names its file (section 4.1). |

**Identity.** The `digest` of a revision covers every field except `digest` and `created`: the rules, the scope,
`previous`, `merges` and `rules_digest`. Because `previous` and `merges` are covered, returning to earlier rules is a
new revision with a new digest and a new file. `created` is not covered. `rules_digest` is a separate field that covers
the rules only, so a reader can compare two revisions' rules without their lineage.

Pin object:

| Key | Type | Meaning |
|---|---|---|
| `snapshot` | digest | The snapshot document's digest (a version 2 snapshot of the dataset). |
| `snapshot_version` | integer, constant `2` | Version of the snapshot document named by `snapshot`. |
| `reason` | string, at least one non-blank character, at most 200 characters, no `/` or `\` | Free text: why the version is kept. |
| `added` | timestamp, UTC | When the pin was added. |

A pin of a digest that is not in the dataset's lineage is refused by the writer and reported by `validate`.
A pin of a digest already pinned is refused by `pin` and reported as `already pinned` by `pin --from-run`.

Example (digests shortened; a real digest has 64 hexadecimal characters):

```json
{
  "retention_version": 1,
  "scope": {"kind": "dataset", "dataset": "raw/run-07"},
  "keep_last": 3,
  "keep_days": 30,
  "hold_copies": 2,
  "pins": [
    {"snapshot": "sha256:4f1c...", "snapshot_version": 2,
     "reason": "version used for the 2026 publication", "added": "2026-11-02T09:15:00Z"}
  ],
  "previous": "sha256:9a07...",
  "created": "2026-11-21T10:00:00Z",
  "rules_digest": "sha256:7d1b...",
  "digest": "sha256:c3e8..."
}
```

Format changes are additive only. A later `retention_version` may add optional keys. A version 1 reader refuses any
key it does not know (SCHEMAS 1 convention).

## 4. Location and revision lineage

### 4.1 Location

Policy documents live in the record area of the project, next to the reference documents:

`<record>/retention/v1/<hex>.json`, where `<hex>` is the hexadecimal part of the revision's `digest` (64 characters).

One file per revision. Area and dataset policies are stored in the same directory; the `scope` key tells them apart.
A writer never overwrites a file: a revision whose file already exists is refused.

### 4.2 Lineage per scope

Revisions follow the same versioning as snapshot documents (SNAPSHOTS 3.4, 6.2):

- Each change writes a new revision whose `previous` names the current head of that scope. Its `merges` is absent
  (section 4.3 is the only exception).
- The head of a scope is the revision that no other revision of the scope names in `previous` or `merges`.
- A write that would leave the rules unchanged writes nothing: the command reports `unchanged` with the head's digest.
- The writer refuses to write a second head. Documents are never edited in place. They are written to a temporary file
  and renamed into place; readers skip temporary files.
- The policy history therefore shows when a pin was added or removed.
- Any invalid file in the folder (unreadable, not a policy document, failing section 3 or a `previous` or `merges` that
  names no revision of the scope) makes every command that reads policies refuse with exit 3 until it is fixed. A
  hidden invalid document could hide a head, so nothing is shown or written past it.

### 4.3 Two heads

After a merge of two clones of the record area, a scope may have two heads. This is an error for every command that
reads the policy. It stays an error until `stradler retention set --merge` writes a revision: its `previous` names the
first head and its `merges` names the second, the heads being ordered by digest, and its rules are the union of both
heads (section 2.1). The `--merge` revision is always written, and the options of `set` apply on top of the union. Any
other command with two heads is refused with exit 3. The tool never picks one head silently.

## 5. Keys in use

| Key | Used by this release |
|---|---|
| `keep_last`, `keep_days` | Stored, shown by `retention show` and written by `retention set`. Evaluation **(later release)**. |
| `hold_copies` | Stored, shown and written by `retention set`. Enforced by the guards **(later release)**. |
| `pins` | Written by `retention pin`, `retention pin --from-run` and `retention unpin`. Evaluation **(later release)**. |

The default for `hold_copies` is `1`. Held locations are counted by the freshness rule of CONTENT-ON-DEMAND
(Decision 7). That rule belongs to the evaluation step and is not needed to write a policy.

## 6. Commands

| Command | Effect | Release |
|---|---|---|
| `stradler retention show <dataset>\|--area A [--json]` | The head revision of the scope, its revision lineage (newest first), and for a dataset the effective rules (section 2.1). | this release |
| `stradler retention set <dataset>\|--area A [--keep-last N\|--no-keep-last] [--keep-days D\|--no-keep-days] [--hold-copies K] [--merge] [--json]` | Writes a new revision with the given rules. Omitted rules keep the head's value; a first revision starts from `keep_last` null, `keep_days` null, `hold_copies` 1 and no pins. `--keep-last` and `--no-keep-last` are exclusive, as are `--keep-days` and `--no-keep-days`. `--keep-last` is at least 1, `--keep-days` at least 0, `--hold-copies` at least 1. | this release |
| `stradler retention pin <dataset> sha256:<hex> --reason TEXT [--json]` | Writes a new revision with the pin added. The digest must be a version of the dataset's lineage and not already pinned. The reason is required (section 3). | this release |
| `stradler retention unpin <dataset> sha256:<hex> [--json]` | Writes a new revision with the pin removed. The digest must be pinned. | this release |
| `stradler retention pin --from-run RUN [--json]` | Writes one revision per input version that the run record names and is not yet pinned (section 6.1). No dataset or snapshot argument. | this release |
| `stradler retention check [<dataset>...] [--area A] [--json]` | Evaluates every version of each lineage (section 7). | later release (evaluation) |

Successful writes print `wrote <digest>` (a new revision) or `unchanged <digest>` (no new revision). With `--json` the
output is the JSON document of the revision and its `written` flag.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success, including `unchanged`. |
| 1 | `retention check` only: a kept version is held by fewer than `hold_copies` locations. **(later release)** |
| 3 | Error: unknown dataset or area, a dataset outside every area, two policy heads without `--merge`, `--merge` without exactly two heads, pin outside the lineage, a digest already pinned (pin) or not pinned (unpin), a reason missing, empty or too long, an invalid policy document, a run record with no input snapshot. |

Retention is never written automatically. No command applies a policy in the background.

### 6.1 Pins from a run record

`retention pin --from-run RUN` takes the run folder or its `run.json`. It reads `inputs.datasets` of the record. Each entry
that carries a `snapshot` digest is one input version of the dataset named by its `path`; entries without `snapshot`
are ignored. The record must name at least one such entry, or the command fails with exit 3.

Every digest is checked against the lineage of its dataset before the first revision is written. Then one revision is
written per input version that is not yet pinned; a version already pinned is reported as `already pinned` and writes
nothing. The reason is the `--reason` text, or `input of run <run_id>` when it is omitted. Each pin is stored like a
manual pin (section 3). A run never adds pins by itself; this form is a manual command.

## 7. Evaluation and `history` **(later release)**

This section is specified now and ships with the evaluation step. It is not available in this release.

For each version of the lineage (CONTENT-ON-DEMAND 11), newest first, the evaluation gives:

- `retention`: `head`, `pinned`, `kept` (with the rule `keep_last`, or `keep_days` with the date it lapses), or
  `droppable`;
- `held`: the number of held locations and their ids. A versioned store counts when its probe shows the version's
  document and every object present;
- `at_risk`: true for a kept version with fewer than `hold_copies` held locations.

`stradler history` gains these three fields per version and a summary line, for example
`raw/run-07  6 versions: head, 1 pinned, 2 kept, 2 droppable; at risk: 1`.

The `history --json` document gains the keys `retention`, `retention_rule`, `held`, `held_locations`, `at_risk` per
version, and `policy` (the policy digests used). This change is additive. `history` keeps its exit codes
(CONTENT-ON-DEMAND 11). `retention check` is the command with a failing exit code, for scheduled runs.

## 8. Guards **(later release)**

This section is specified now and ships with the guard step. It is not available in this release.

The rule: no stradler operation removes the last held copy of a kept version.

- **drop** acts on the working copy, which holds only the head, and already requires `min_copies` held copies
  elsewhere. It additionally requires `hold_copies` of the effective policy when that is higher. No other change.
- **put** to a plain store (a later phase of CONTENT-ON-DEMAND) replaces a file only when its sha256 equals
  `replace_if_sha256`. When the location is a held location of a kept older version and replacing the file would end
  that version's match there, `put` refuses with `retention.last_copy`. It proceeds only if the version stays held at
  `hold_copies` other locations, or the target is a versioned store (which keeps the old version).
- **seal** writes a new head, so a version may change from `kept` to `droppable` (count rule), and the outgoing head
  becomes an older version. Seal prints the retention changes. When the outgoing head is kept and held nowhere, the old
  bytes are already gone from the working copy at seal time, so a refusal would protect nothing. Seal therefore warns
  (`retention.unheld`) and the content record of the seal says so. The protection is `retention check` before editing.
  Seal never refuses for this reason.
- **store prune** of a versioned store never removes a kept version unless `--force-kept` is given and the version stays
  held at `hold_copies` other locations. It never removes the head. The prune command is specified with the versioned
  store and is outside this document.

## 9. Known limits

- A kept version can be lost by editing the working copy before any store holds it. Seal can only warn. A scheduled
  `retention check` is the defence.
- `keep_days` depends on the `created` times written by other machines. A wrong clock shifts the lapse date. The date
  is shown, and pins are not affected by it.
- Freshness of `held` relies on recorded probe results. A location that lost content after its last match gives a false
  count, bounded by the freshness limit.
- In this release a policy write leaves no content record. The `retention_set` content record of
  `docs/plans/VERSIONS.md` 2.4 **(later release)**.
