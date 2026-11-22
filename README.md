# stradler

stradler (strata handler) is a framework for declaring layered project data structures and running workflows over
them. Source and issues: https://github.com/dvm-shlee/stradler.

## Status

Alpha (`0.1.0a1`). No PyPI or crates.io release has been published yet; the `stradler` names on both are reserved.

### Implemented on main

- `stradler init --template <file>`: create `stradler.json` and `.stradler/` from a project template.
- `stradler validate [path]`: validate the project at or above a directory (`--json` for machine output). It checks
  the `structure@1.1.0` roles and grades and the `config@1.1.0` replica declarations
  ([docs/SCHEMAS.md](docs/SCHEMAS.md)). A file under no declared area is a warning (`validate` stays ok).
- `stradler adopt [path]`: take an existing directory in as a project (`--template`, `--project-id`, `--dry-run`,
  `--json`); copy mode with `--plan <file>` copies files in from a plan (`--dry-run` reports every problem without copying;
`--no-keep-times`, `--bwlimit`, `--progress-interval`). See [docs/ADOPT.md](docs/ADOPT.md).
- `stradler describe [path]`: human text, `--json` (versioned document, `describe_version` 2) and `--agent` views of
  `.stradler/`. See [docs/DESCRIBE.md](docs/DESCRIBE.md).
- `stradler seal <dataset>`: make a dataset of a sealed area read-only and write its version 2 snapshot document
  (`--layout flat|tree`, `--stream`, `--grade`, `--provenance-file`, `--rollback`, `--json`).
- `stradler verify <dataset | sha256:digest>`: check a dataset, or a copy in a store (`--store NAME`), or one subtree
  (`--subtree`), against its snapshot; read-only. `stradler verify --replicas <area>` checks the area's latest
  snapshots against its declared replica stores and records the checks. Exit status: 0 match, 1 differences,
  2 unreachable store, 3 error (including a usage error).
- Snapshot format 2 (`snapshot_version` 2: flat and tree layouts, content digests, lineage): written by `seal`.
  Format 1 is still read and is what `export_sealed` in `stradler.workflow.export` writes. See
  [docs/SNAPSHOTS.md](docs/SNAPSHOTS.md).
- Workflow planning and execution (Python API): `build_plan` and `run_workflow` in `stradler.workflow`.
- Run records: `run.json` and `task.json` written per run, with digests and container objects.
- Scheduler add-on `stradler-slurm` (`stradler.ext.slurm`); container runtime add-on `stradler-apptainer`
  (`stradler.ext.apptainer`).
- Job-side entry points used by scheduler add-ons: `stradler workflow chain` and `stradler workflow finalize`.

### Documented, not implemented

- `stradler upgrade` (re-materialise `.stradler/` from a newer template).
- A `stradler workflow run` command: planning and execution are reachable only through the Python API for now.

### Other

- `rust/` holds the crates.io name reservation (`stradler` 0.0.0, no functionality).
- `schemas/` holds the JSON Schema files for `stradler.json`, `.stradler/config.toml` (`config@1.0.0`, `config@1.1.0`)
  and `.stradler/structure.toml` (`structure@1.0.0`, `structure@1.1.0`).

## Documentation

- [docs/GLOSSARY.md](docs/GLOSSARY.md): normative definitions of stradler terms.
- [docs/SCHEMAS.md](docs/SCHEMAS.md): project file layout and schemas.
- [docs/ADOPT.md](docs/ADOPT.md): taking an existing directory in as a project, and copy mode.
- [docs/DESCRIBE.md](docs/DESCRIBE.md): `stradler describe` output, including the `--json` document.
- [docs/WORKFLOWS.md](docs/WORKFLOWS.md): workflow recipes, plans, tasks, run records and conformance data.
- [docs/ADDONS.md](docs/ADDONS.md): scheduler and container runtime add-on protocols.
- [docs/SNAPSHOTS.md](docs/SNAPSHOTS.md): snapshot format (versions 1 and 2), digests and checks.

Add-on guides: [docs/addons/slurm.md](docs/addons/slurm.md), [docs/addons/apptainer.md](docs/addons/apptainer.md).

## Development

Install with `uv sync`; run the tests with `uv run pytest`.
