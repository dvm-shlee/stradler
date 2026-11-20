# stradler project file schemas

Status: draft (2026-10-29). Terms are defined in `GLOSSARY.md`.

This document specifies the files that declare a stradler project: the root marker `stradler.json`, the project
configuration `.stradler/config.toml` and the structure `.stradler/structure.toml`, together with the other project
files of the layout (section 2). The JSON Schema files `stradler@1.0.0.json`, `config@1.0.0.json`,
`structure@1.0.0.json`, `structure@1.1.0.json` and `config@1.1.0.json` implement sections 3 to 5.

Each field is marked with its origin:

- **design**: stated by the stradler design;
- **proposed**: needed to make the field implementable; the design leaves it open. Proposed items are collected in
  section 8.

## 1. Conventions

- Machine-written files are JSON. Human-edited project files are TOML.
- JSON Schema draft 2020-12. A TOML document is validated as the JSON data model of its parsed content: tables become
  objects; strings, integers, booleans and arrays map directly. The schemas for TOML files do not use TOML datetimes
  or floats. (proposed)
- Schema references take one of two forms (design): `<name>@<MAJOR>.<MINOR>.<PATCH>`, which names a file
  `<name>@<version>.json` in `.stradler/schema/`; or `sha256:<64 hex digits>`, the digest of that file.
- A schema file is never edited in place. A change adds a new version. (design)
- Every table defined here rejects keys that are not listed. Extension data goes in the `settings` table of an
  add-on in `local.toml` (section 6.2). (proposed)
- Names of areas, add-ons, stores and pins match `^[A-Za-z0-9_-]+$`. (proposed; area names are otherwise free, design)

File formats (design, section 3.3.1):

| File | Format | Schema |
|---|---|---|
| `stradler.json` | JSON | `stradler@1.0.0` (section 3) |
| `.stradler/config.toml` | TOML | `config@1.0.0` (section 4) or `config@1.1.0` (section 4.1) |
| `.stradler/structure.toml` | TOML | `structure@1.0.0` (section 5) or `structure@1.1.0` (section 5.1) |
| `.stradler/template.json` | JSON | none in this specification (section 6.1) |
| `.stradler/local.toml` | TOML | none in this specification (section 6.2) |
| `.stradler/schema/*.json` | JSON Schema | the file itself |
| Workflow recipes | YAML, strict subset | outside this document |
| Run records | JSON | outside this document |
| Snapshot documents | JSON | `SNAPSHOTS.md` |

## 2. Project layout

Only two things at the project root are fixed (design, section 3.3):

```
<project>/
  stradler.json        # marker: "this directory is a stradler project" (JSON, a few lines)
  .stradler/           # git-tracked, small, rarely changed
    config.toml        # add-ons and versions, true sources, workspace pins
    structure.toml     # areas, properties, manifest rules, agent access (materialized at init)
    template.json      # the project template it was made from: name, version, digest
    schema/            # versioned schemas, never edited in place: core, handler (e.g. BIDS), user
    local.toml         # machine-local settings (real paths, endpoints); git-ignored
```

- Tools find a project by walking up to the nearest `stradler.json`, as git does with `.git`. (design)
- Manifests below the root are not fixed. `structure.toml` declares which folders carry a manifest, and in what
  format (section 5, `manifest`). (design)
- Records, logs, the data engine's object store and caches, and the agent workspace are areas of their own, outside
  `.stradler/`. (design)
- Files in `.stradler/` other than `local.toml` are tracked in git. `local.toml` is git-ignored. (design)
- `stradler describe` renders `.stradler/` for humans, and `--json` for agents and batch jobs (`DESCRIBE.md`). (design)

## 3. `stradler.json` (schema `stradler@1.0.0`)

The root marker. It is a JSON object with exactly the four fields below, all required.

| Field | Type | Origin | Meaning |
|---|---|---|---|
| `marker` | string, constant `"stradler-project"` | design | Marks the directory as a stradler project. |
| `spec_version` | string, `MAJOR.MINOR.PATCH` | design | Version of this specification that the project follows. |
| `project_id` | string, at least one character | design | Identifier of the project. |
| `config_dir` | string, constant `".stradler"` | design (location); value fixed by the decision that the directory is named `.stradler/` | Location of the `.stradler/` directory, relative to the project root. |

Example:

```json
{
  "marker": "stradler-project",
  "spec_version": "1.0.0",
  "project_id": "example-project",
  "config_dir": ".stradler"
}
```

## 4. `.stradler/config.toml` (schema `config@1.0.0`)

Human-edited and git-tracked. It holds add-ons and their versions, true sources and workspace pins (design). It holds
logical names only: real paths, endpoints and credentials are kept in `local.toml` (section 6.2). The configuration
has no other content.

Top-level key:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `schema` | string, schema reference | yes | proposed (the design shows the key for `structure.toml`) | Schema reference of this file, for example `config@1.0.0`. |

Table `[addons.<name>]`: one per add-on the project uses. Add-ons are separately installed components (design,
section 6).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `kind` | enum: `scheduler`, `container_runtime`, `data_handler`, `store`, `remote` | yes | design (kinds of sections 6 and 7.1); values proposed | The protocol the add-on implements. |
| `version` | string, `MAJOR.MINOR.PATCH` | yes | design ("add-ons and versions") | Version of the add-on used by the project. |
| `command` | string, at least one character | yes | proposed (section 6: the add-on is a command-line program) | Program that implements the protocol: one JSON request on stdin, one JSON response on stdout. |

Table `[stores.<name>]`: a logical store name that areas and true sources refer to (design, section 3.5).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `addon` | string, name | yes | proposed | Name of an `[addons]` entry whose `kind` is `store` or `remote`. |

Table `[true_sources.<area>]`: the true source of an area's content, if the content is not stored in place (design,
section 3.3, "true sources"; section 3.2, `source`).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `store` | string, name | yes | design; key proposed | Name of a `[stores]` entry that holds the authoritative copy. `<area>` is an area name in `structure.toml`. |

Table `[pins.<name>]`: workspace pins (design, section 3.3; section 9 for the stradler revision that a package is built
against).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `ref` | string | yes | proposed | Pinned version, git revision, or digest (`sha256:<64 hex digits>`) of the component named by `<name>`. |

Example (illustrative):

```toml
schema = "config@1.0.0"

[addons.slurm]
kind = "scheduler"
version = "0.1.0"
command = "stradler-scheduler-slurm"

[addons.apptainer]
kind = "container_runtime"
version = "0.1.0"
command = "stradler-runtime-apptainer"

[addons.nas]
kind = "store"
version = "0.1.0"
command = "stradler-store-nas"

[stores.shared-sif]
addon = "nas"

[true_sources.rawbids]
store = "shared-sif"

[pins.stradler]
ref = "0.1.0a1"
```

## 4.1 `.stradler/config.toml` (schema `config@1.1.0`)

`config@1.1.0` adds the `replicas` table to `config@1.0.0`. `config@1.0.0` is unchanged: a project that declares it
stays valid, and a `replicas` table in a file that declares `config@1.0.0` is an unknown key. The schema is chosen by
the `schema` key.

Table `[replicas.<area>]`: where copies of an area must exist. `<area>` is an area name in `structure.toml`. It is
kept apart from `[true_sources]`: a true source says which store is authoritative; a replica declaration says which
other stores must hold a copy, and what checks are recorded for them (`SNAPSHOTS.md` section 7.6).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `stores` | non-empty array of names, unique | yes | proposed | Names of `[stores]` entries that must hold a copy of the area's sealed datasets. |
| `min_copies` | integer, at least 1 | no | proposed | How many of the declared stores must hold a copy whose last check matched. At most the number of `stores`. |

Rules checked by `validate`, in addition to the schema: `<area>` is an area of `structure.toml`; every name in
`stores` is a defined store; a store is not the area's own store (its `store` or `source`, else its
`[true_sources.<area>]` entry); `min_copies` is not more than the number of declared stores.

Example (illustrative):

```toml
schema = "config@1.1.0"

[stores.shared]
addon = "nas"

[stores.offsite]
addon = "nas"

[replicas.rawdata]
stores = ["shared", "offsite"]
min_copies = 2
```

## 5. `.stradler/structure.toml` (schema `structure@1.0.0`)

The materialized, project-owned declaration of areas and their properties. It is written by `stradler init` from the
project template, and read on every command (design). Its keys are as follows.

Top-level key:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `schema` | string, schema reference | yes | design (section 3.5) | Schema reference, for example `structure@1.0.0`. |
| `areas` | table of `[areas.<name>]` tables | yes, at least one area | design (section 3.5) | The areas of the project. |

Table `[areas.<name>]`: one per area. Area names and paths are free (design, section 3.1); the names are constrained
as in section 1.

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `role` | enum: `input`, `output`, `code`, `recipe`, `image`, `record`, `agent` | yes | design (section 3.1, closed list) | The role of the area. |
| `path` | string, relative folder | yes, unless `store` is set; not allowed together with `store` | design (path); exclusivity proposed | Folder of the area, relative to the project root. It must not be empty, absolute, contain a `..` segment, or be `.` or lie inside `.stradler/` (proposed). For role `agent` the path is never the project root (design, section 3.1). |
| `store` | string, name | no | design (section 3.5, `store = "shared-sif"`) | Name of a `[stores]` entry in `config.toml` where the area's content lives, outside the project. |
| `handler` | string, name | no | design (section 3.2) | Data handler that indexes the area, for example `bids` or `checkpoint`. The name must match a configured add-on of kind `data_handler` (checked by `validate`, section 7). |
| `mutability` | enum: `immutable`, `sealed`, `mutable` | yes | design (section 3.2); required proposed | `immutable`: never written by any step. `sealed`: written once by its producing workflow, then frozen. `mutable`: may be rewritten or deleted. Required on every area; there is no default. |
| `deletable_after` | array of area names, unique | no | design (section 3.2, "list of datasets"); area names proposed | The area may be deleted once these areas exist as sealed snapshots. Default: empty. |
| `manifest` | string, file name | no | design (section 3.3, "in what format"); format proposed | Name of the manifest file in the root folder of the area, for example `dataset_description.json`. The format follows from the extension; only `.json` is allowed in this version (proposed). |
| `source` | string, name | no | design (section 3.2) | Name of a `[stores]` entry that is the true source of the area's content. If `[true_sources.<area>]` is also set, both must name the same store (proposed). |
| `agent` | table with keys `search`, `read`, `write` (booleans) | no | design (sections 3.2 and 3.4) | Agent access. Each key defaults to `false` (proposed). `search`: query the metadata layer. `read`: read file contents. `write`: create or change files. |

Rules checked by `validate`, not by the JSON Schema:

- `write = true` is allowed only when the mutability is `mutable`. A write on an `immutable` or `sealed`
  area is refused (design, section 3.4; the `sealed` case is a proposed reading).
- Every name in `store` and `source` is a `[stores]` entry; every name in `handler` is a configured data handler.
- Every name in `deletable_after` is an area of `structure.toml`.
- No two areas have the same `path`. (proposed)
- Agent settings are published in the manifest and through `stradler describe --agent` (design, section 3.4). The
  enforcement is the agent harness's job; stradler does not depend on any harness (design).

Example (design section 3.5, illustrative; it is intended to validate against `structure@1.0.0`):

```toml
schema = "structure@1.0.0"

[areas.rawbids]
role = "input"
path = "rawbids"
handler = "bids"
mutability = "immutable"
manifest = "dataset_description.json"
agent = { search = true, read = true, write = false }

[areas.sourcedata]
role = "input"
path = "sourcedata"
mutability = "immutable"
agent = { search = true, read = false, write = false }   # names only

[areas.checkpoints]
role = "output"
path = "checkpoints"
handler = "checkpoint"
mutability = "mutable"
deletable_after = ["derivatives"]

[areas.derivatives]
role = "output"
path = "derivatives"
handler = "bids"
mutability = "sealed"
manifest = "dataset_description.json"

[areas.reports]
role = "output"
path = "reports"
mutability = "mutable"

[areas.code]
role = "code"
path = "code"
mutability = "mutable"

[areas.recipes]
role = "recipe"
path = "recipes"
mutability = "mutable"

[areas.images]
role = "image"
mutability = "immutable"
store = "shared-sif"          # defined in config.toml, outside the project

[areas.records]
role = "record"
path = "records"
mutability = "mutable"

[areas.agent]
role = "agent"
path = "agent"
mutability = "mutable"
agent = { search = true, read = true, write = true }
```

## 5.1 `.stradler/structure.toml` (schema `structure@1.1.0`)

`structure@1.1.0` adds user-defined roles and the area grade to `structure@1.0.0`. `structure@1.0.0` is unchanged: a
project that declares it stays valid, and its areas are read exactly as before. Every key of section 5 keeps its
meaning, except that `role` is a string that names a core role or a role defined in `roles` (below). The two schemas
are chosen by the `schema` key; a `roles` table or a `grade` key in a file that declares `structure@1.0.0` is an
unknown key.

Top-level key added:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `roles` | table of `[roles.<name>]` tables | no | proposed | User-defined roles, by name (name pattern of section 1). |

Table `[roles.<name>]`:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `base` | enum: the seven core roles | yes | proposed | The core role the engine treats areas of this role as. One level only: a base is never a user role. |
| `description` | string | no | proposed | What areas of this role are for. |
| `mutability` | non-empty array of `immutable`, `sealed`, `mutable`, unique | no | proposed | The mutabilities an area of this role may use. Default: all three. |
| `agent` | table with keys `search`, `read`, `write` (booleans) | no | proposed | Upper bound of the `agent` keys of an area of this role: an area may not grant what the role denies. Each key defaults to `false` once the table is given; without the table, the role sets no bound. |
| `min_grade` | enum of grades | no | proposed | The least strict grade an area of this role may have. |

The engine (plan, execute, export, snapshot) treats an area of a user role exactly as an area of its base role. A role
may only narrow its base, never loosen it: it can restrict mutability, bound agent access and require a grade, and it
cannot grant anything the base role or the mutability rules refuse.

Key added to every `[areas.<name>]` table:

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `grade` | enum: `public`, `work`, `confidential`, `private` | no | proposed | The widest audience the content of the area may be disclosed to. Ordered from least to most strict: `public` < `work` < `confidential` < `private`. Absent means `private` (fail-closed). |

The grade is a declaration. stradler declares it and checks the rules below; consumers (agents, exporters, report
tools) enforce it. The grade is independent of the `agent` keys, which say what an agent may do in an area: an `agent`
key never loosens a grade, and a grade never grants an agent key. The `grade` of a snapshot document
(`SNAPSHOTS.md` section 3.1) refers to this definition.

Rules checked by `validate` for `structure@1.1.0`, in addition to those of section 5:

- A role name must not equal a core role; `base` must be a core role.
- An area's `role` must be a core role or a name defined in `roles`.
- An area's `mutability` must be in the allowed set of its role.
- An area's `agent` key may be `true` only if the role's `agent` table allows it (when the role has one).
- A role with `agent.write = true` must allow `mutable` in its `mutability`: a write is never above mutability
  (section 5), so such a role would loosen its base.
- An area's grade must be at least as strict as the `min_grade` of its role; an area without `grade` is `private`.

Plan-time rule (`stradler` workflow plan, WORKFLOWS.md): a step whose outputs go to an area with a grade looser than the
strictest grade among the areas of its inputs is refused with a message that names the step, the output and the input.
The inputs of a step are the dataset, the fixed and named inputs and the outputs of the steps it reads; an input or
output outside every area, or an output template that starts with a placeholder, is not graded. A project without
grades has only `private` areas and is never refused.

Example, with the standard extension roles:

```toml
schema = "structure@1.1.0"

[roles.archive]
base = "input"
description = "Deposits that are kept as they were received."
mutability = ["immutable", "sealed"]

[roles.reference]
base = "input"
description = "Material consulted but never changed."
mutability = ["immutable", "sealed"]

[roles.media]
base = "input"
description = "Audio, image and video files."
mutability = ["immutable", "sealed"]

[roles.document]
base = "input"
description = "Documents that people edit."
mutability = ["mutable"]

[areas.deposits]
role = "archive"
path = "deposits"
mutability = "sealed"
grade = "confidential"

[areas.library]
role = "reference"
path = "library"
mutability = "immutable"
grade = "public"

[areas.recordings]
role = "media"
path = "recordings"
mutability = "immutable"

[areas.notes]
role = "document"
path = "notes"
mutability = "mutable"
grade = "work"
agent = { search = true, read = true, write = true }

[areas.results]
role = "output"
path = "results"
mutability = "mutable"
grade = "private"
```

Here a step that reads `notes` (`work`) may write to `results` (`private`) but not to an area of grade `public`.

## 6. Other project files

### 6.1 `.stradler/template.json`

The project template the project was made from (design, section 3.3). It is written by `stradler init` and
`stradler upgrade`. It is a JSON object with three required fields.

| Field | Type | Meaning |
|---|---|---|
| `name` | string, at least one character | Name of the project template. |
| `version` | string, `MAJOR.MINOR.PATCH` | Version of the project template. |
| `digest` | string, `sha256:<64 hex digits>` | Digest of the template content the project was made from. |

No schema file is assigned to this file in this version (proposed).

### 6.2 `.stradler/local.toml`

Machine-local settings: real paths and endpoints (design, section 3.3). It is git-ignored. Credentials of add-ons live
only in add-ons and in this file (design, section 7.1). No key defined here is read by the core for credentials.

| Table | Keys | Type | Meaning (proposed) |
|---|---|---|---|
| `[stores.<name>]` | `path` | string, absolute path | Local directory of the store `<name>` defined in `config.toml`. |
| `[stores.<name>]` | `endpoint` | string | Endpoint (URL or host) of the store `<name>`. |
| `[addons.<name>]` | `settings` | table, free content | Settings that the add-on `<name>` reads. The core does not validate its content. |

Every `<name>` must exist in `config.toml`. The core never writes credentials to records (design, section 7.1).

### 6.3 `.stradler/schema/`

Versioned schemas, one file per version: `<name>@<MAJOR>.<MINOR>.<PATCH>.json` (design, section 3.3). The directory
holds three kinds: core (`stradler`, `config`, `structure`), handler (for example BIDS) and user. A project holds a copy
of every schema it references, so that it can be validated offline (design). Files are never edited in place.

## 7. Validation

- The JSON Schema for each file checks its shape: required keys, types, enums, patterns, and unknown keys.
- The rules listed in sections 4 and 5 check values against other files and against the mutability and agent rules.
  They are run by `stradler validate` (design, section 5), not by the JSON Schema.
- Validation needs no network access: a project and its schemas are self-contained (design, section 3.3).

## 8. Points not fixed by the design

These points are proposed in this document. Each can be changed before the schemas are released.

1. `schema` key in `config.toml` (section 4).
2. Kind values `container_runtime`, `data_handler`, `store`, `remote` as enum values; `remote` comes from section 7.1.
3. The `command` key of an add-on (section 4).
4. `mutability` is required on every area (no default); every `agent` key defaults to `false`.
5. `write = true` is refused on a `sealed` area (section 5).
6. `deletable_after` names areas, as in the design example; section 3.2 says datasets.
7. `manifest` format follows from the extension; only `.json` is allowed.
8. `source` and `[true_sources]` may both be set, and must agree.
9. Exclusivity of `path` and `store`; `path` may not be `.` or inside `.stradler/`; no two areas share a path.
10. Pin format in `[pins]` (section 4).
11. Unknown keys are errors in every table defined here.
12. Name pattern `^[A-Za-z0-9_-]+$` for area, add-on, store and pin names.
13. Built-in add-ons (the local scheduler and the no-container runtime, design section 6) need no `[addons]` entry.
14. No schema file is assigned to `template.json` or `local.toml` (section 6).

Points of `structure@1.1.0` (section 5.1) proposed here:

15. Roles are one level deep; `description` is optional; a role `agent` table is an upper bound for the area keys.
16. A role may not allow `agent.write` unless it allows `mutable`.
17. Grades are the four values `public`, `work`, `confidential`, `private`, in that order; absent means `private`.
18. The plan-time grade rule compares the areas of a step's inputs with those of its outputs, not file contents.

Resolved by `structure@1.1.0`: the meaning of the reserved `grade` of snapshot documents (`SNAPSHOTS.md` section 3.1);
the earlier points 1 to 14 are unchanged.
