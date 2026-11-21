# Retention per version

Status: normative text, draft for the first retention release. Items marked **(later release)** are specified here
but are not implemented in the first release.

Scope of this document: the retention policy document, where it lives, how its revisions form a lineage, its keys,
its command-line interface, and the rules that evaluate and guard versions. Version diff and the version-keeping store
are specified elsewhere and are not repeated here. Other section references ("SNAPSHOTS 3.4", "WORKFLOWS 10.1") point
into `docs/`.

## 1. Terms

| Term | Meaning |
|---|---|
| retention policy | A versioned JSON document for one dataset or one area that states which versions of a dataset's lineage must stay held (section 3). |
| scope | The dataset or area a policy applies to (section 2). |
| kept version | A version that the applicable policies keep: the head, a pinned version, or a version within `keep_last` or `keep_days`. **(later release, evaluation)** |
| droppable version | A version of the lineage that no applicable policy keeps. It may still be held; nothing removes it automatically. **(later release, evaluation)** |
| held version | A version for which at least one location of its reference has a full match no older than the freshness limit, or a versioned store that holds it. The working copy never counts. **(later release, evaluation)** |
| lineage | The chain of snapshot versions of one dataset, linked by each version's `previous` digest. |
| head | The newest version of a lineage: the version no other version of the lineage names in `previous`. |

## 2. Scope

A policy applies to one dataset, or to every dataset of one area. Both kinds may exist at the same time.

- A version is kept when any applicable policy keeps it: the rules of the scopes combine as a **union**. A dataset
  policy can add pins and widen limits, but it can never weaken the rules of its area.
- The head of a dataset is always kept, with or without a policy.
- A dataset with no applicable policy keeps only its head. Nothing protects older versions until a policy names them.

## 3. Document (`retention_version` 1)

The policy is a record, not a path list. It names versions by content digest and never lists files.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `retention_version` | integer, constant `1` | yes | Version of this format. |
| `scope` | object | yes | `{"kind": "dataset", "dataset": "<project-relative folder>"}` or `{"kind": "area", "area": "<area name>"}`. |
| `keep_last` | integer, at least 1, or `null` | yes | Keep the newest N versions of the lineage, the head included. `null` means no count rule. |
| `keep_days` | integer, at least 0, or `null` | yes | Keep a version until D days after it was superseded. The clock starts at the `created` time of the version whose `previous` names it. `null` means no time rule. |
| `hold_copies` | integer, at least 1 | no, default `1` | How many held locations each kept version must have. |
| `pins` | array of pin objects, sorted by `snapshot` | yes, may be empty | Versions kept regardless of the other rules. Must be empty when `scope.kind` is `area`. |
| `previous` | digest or `null` | yes | Digest of the earlier revision of the policy for the same scope; `null` for the first revision. |
| `merges` | array of digests, sorted, may be empty | yes | The other heads of the scope that this revision merges (section 4.3). Empty except for a `--merge` revision. |
| `created` | timestamp, UTC | yes | When this revision was written. |
| `rules_digest` | digest | yes | Digest of the canonical JSON (WORKFLOWS 10.1, 10.2) of the rule keys only: `retention_version`, `scope`, `keep_last`, `keep_days`, `hold_copies` and `pins`. Two revisions with equal rules have equal `rules_digest`. |

The identity of a revision is the digest of the canonical JSON (WORKFLOWS 10.1, 10.2) of the whole document, including
`previous`, `merges`, `created` and `rules_digest`. The file is named by it (section 4.1). A document never holds its
own digest, so no field refers to itself.

Pin object:

| Key | Type | Meaning |
|---|---|---|
| `snapshot` | digest | The snapshot document's digest. |
| `snapshot_version` | integer, constant `2` | Version of the snapshot document named by `snapshot`. |
| `reason` | string, at most 200 characters, no paths | Free text: why the version is kept. |
| `added` | timestamp, UTC | When the pin was added. |

A pin of a digest that is not in the dataset's lineage is refused by the writer and reported by `validate`.

Example (digests shortened):

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
  "merges": [],
  "created": "2026-11-21T10:00:00Z",
  "rules_digest": "sha256:7d1b..."
}
```

Format changes are additive only. A later `retention_version` may add optional keys. A version 1 reader refuses any
key it does not know (SCHEMAS 1 convention).

## 4. Location and revision lineage

### 4.1 Location

Policy documents live next to the reference documents, in the record area:

`<record>/retention/v1/<hex>.json`, where `<hex>` is the hexadecimal part of the digest of the whole canonical document of
the revision (section 3, identity).

One file per revision. Area and dataset policies are stored in the same directory; the `scope` key tells them apart.

### 4.2 Lineage per scope

Revisions are versioned the same way as snapshot documents (SNAPSHOTS 3.4, 6.2):

- Each change writes a new document whose `previous` names the current head of that scope. Its `merges` is empty.
- The head of a scope is the revision that no other revision of the scope names in `previous` or `merges`.
- A writer refuses to write a second head. Documents are never edited in place. They are written to a temporary file
  and renamed into place.
- The policy history therefore shows when a pin was added or removed.

### 4.3 Two heads

After a merge of two clones of the record area, a scope may have two heads. This is an error for every command that
reads the policy. It stays an error until `stradler retention set --merge` writes a revision whose `previous` names one
head, whose `merges` names the other head, and whose rules are the union of both heads. The tool never picks one head
silently.

## 5. Keys in use

| Key | Used by this release |
|---|---|
| `keep_last`, `keep_days` | Stored and shown by `retention show` and `set`. Evaluation **(later release)**. |
| `hold_copies` | Stored and shown. Enforced by the guards **(later release)**. |
| `pins` | Written by `retention pin` and `unpin`. Evaluation **(later release)**. |

The default for `hold_copies` is `1`. Held locations are counted by the freshness rule of CONTENT-ON-DEMAND
(Decision 7). That rule belongs to the evaluation step and is not needed to write a policy.

## 6. Commands

| Command | Effect | Release |
|---|---|---|
| `stradler retention show <dataset>\|--area A [--json]` | The head policy of the scope, its revision lineage, and the effective rules for a dataset (area and dataset combined). | this release |
| `stradler retention set <dataset>\|--area A [--keep-last N\|--no-keep-last] [--keep-days D\|--no-keep-days] [--hold-copies K] [--merge]` | Writes a new revision. | this release |
| `stradler retention pin <dataset> sha256:<hex> --reason TEXT` | Writes a new revision with the pin added. | this release |
| `stradler retention unpin <dataset> sha256:<hex>` | Writes a new revision with the pin removed. | this release |
| `stradler retention pin <dataset> --from-run <run record>` | Writes a new revision that pins every input version of that run record (WORKFLOWS 9). Manual pins and this form are the only ways a pin is added; a run never adds pins automatically. | this release |
| `stradler retention check [<dataset>...] [--area A] [--json]` | Evaluates every version of each lineage (section 7). | later release (evaluation) |

Every write also writes a content record (CONTENT-ON-DEMAND 12) with `op` `retention_set` and the policy digest. The
record holds no paths.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | `retention check` only: a kept version is held by fewer than `hold_copies` locations. |
| 3 | Error: unknown dataset or area, two policy heads, pin outside the lineage, invalid document. |

Retention is never written automatically. No command applies a policy in the background.

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
