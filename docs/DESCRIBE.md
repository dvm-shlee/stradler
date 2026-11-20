# stradler describe

`stradler describe [path] [--json] [--agent]` describes the project at or above `path` (default `.`). Human text is the
default. `--json` prints the public document below. `--agent` reduces the output to what agents need. The two flags
combine. Exit status: 0 on success, 1 when no project is found or a project file is missing or does not parse.

The command reads `stradler.json`, `.stradler/config.toml` and `.stradler/structure.toml`. It never reads
`.stradler/local.toml` into its output: no path, endpoint or setting from it, and no credential, appears.

## Document (`describe_version` 2)

Keys appear in the order shown; every list is sorted by name, so the output of an unchanged project is
byte-identical. Absent values are `null`.

| Key | Content |
|---|---|
| `describe_version` | Integer, 2. |
| `project` | `name` (project_id), `spec_version`, `config_schema`, `structure_schema`. |
| `areas` | One entry per area: `name`, `path`, `role` (as declared), `base_role` (the core role the engine uses), `mutability`, `grade` (`private` when the area declares none), `store`, `manifest`, `agent` (`search`, `read`, `write`, each an explicit boolean), `deletable_after` (sorted). |
| `user_roles` | `name`, `base`, `description`, `mutability` (all three when not restricted), `agent` (the upper bound, or `null`), `min_grade`. Empty for `structure@1.0.0`. |
| `stores` | Store names only. |
| `addons` | `name`, `kind`. |
| `pins` | `name`, `ref`. |
| `replicas` | One entry per (area, store) declared in `[replicas]` (`config@1.1.0`), sorted by area then declaration order: `area`, `store`, `declared` (true), `last_check` (UTC time of the newest check of the area's latest snapshots, or `null`), `result` (`match`, `differs`, `unreachable`, `partial` when some latest snapshot has no check for the store, or `null` when none has), `min_copies` (integer or `null`), `min_copies_met` (true when at least `min_copies` declared stores have result `match`; `null` without `min_copies`). Empty without declarations. Read from recorded checks only; nothing is checked. |

`store` of an area is its `store`, else its `source`, else the `[true_sources]` entry of that name.
Version 1 had no `replicas` key; version 2 adds it after `pins`. Run `stradler verify --replicas <area>` to record
new checks.

## Agent view

`--agent` prints `describe_version` and, per area, `area`, `role`, `agent` and `grade`. Text form: one line per area.

## Version rule

A change of the shape of the document (a key removed, renamed, retyped, added or reordered) raises
`describe_version`. A consumer checks `describe_version` and refuses a value it does not know.
