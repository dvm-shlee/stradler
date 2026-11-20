# stradler glossary

Status: draft (2026-10-29).

Definitions are normative. A term not in this list is not a stradler term.

Docstrings, CLI help, schema descriptions and documents use these definitions verbatim. Section numbers in
parentheses refer to the stradler design document.

| Term | Definition |
|---|---|
| project | A directory tree that holds a `stradler.json` marker and a `.stradler/` directory at its root. |
| project template | A reusable declaration of a project's structure (areas, properties, manifest rules, agent access). Read only by `stradler init` (and `stradler upgrade`); its content is materialized into `.stradler/`. |
| structure | The materialized, project-owned declaration of areas and their properties (`.stradler/structure.toml`). Read on every command. |
| workflow recipe | A declarative document that defines a workflow; read on every run. |
| container recipe | The build definition of a container image in the runtime's own format (Apptainer `.def`, Dockerfile). |
| schema | A JSON Schema document that defines what a valid recipe or metadata file is. |
| area | A named, recipe-declared region of a project with a role (section 3.1), a path and properties. |
| dataset | A versioned collection of files inside an area, described by one manifest. Datasets nest: a dataset can have parent and child datasets. |
| manifest | The metadata file at the root of a dataset (or any declared folder): identity, schema reference, properties, and the content digest of its files. |
| metadata layer | The set of all manifests and schema references in a project; it is enough to understand the project without reading data files. |
| artifact | One file or directory produced or consumed by a step, identified by its content digest. |
| intermediate artifact | An artifact kept only until the artifacts derived from it exist. |
| digest | A cryptographic hash of content (algorithm named with the value, e.g. `sha256:...`). |
| snapshot | An immutable, digest-identified version of a dataset: a Merkle manifest of its files. |
| pin | A reference to a snapshot, image or recipe by digest, so that it cannot change silently. |
| mutability | A dataset property: `immutable`, `sealed` or `mutable` (section 3.2). |
| workflow | A directed acyclic graph (DAG) of steps, declared by a workflow recipe. |
| step | One node of a workflow: a command template, its inputs, outputs, container and resources. |
| task | One execution of a step for one unit of input. |
| job | What a scheduler runs: one task, or several tasks grouped (for example a Slurm array). |
| run | One execution of a workflow (or a subset of it), identified by a run id, with a run record. |
| run record | The provenance of a run: recipe digests, input snapshots, parameters, image digests, framework version, task states and exit codes. |
| join | A step that waits for all tasks of its upstream step. |
| plan | The resolved list of tasks of a run, computed before anything executes. |
| current | A task is current when a record shows the same step, inputs, parameters and image digests; current tasks are skipped. |
| scheduler | An add-on that places jobs: local threads, Slurm, Kubernetes, others. |
| container runtime | An add-on that builds images from container recipes and runs commands inside them: Apptainer, Docker, Podman, others. |
| data handler | An add-on that indexes and queries a dataset of a given standard: BIDS (through pybids), the checkpoint layout, others. |
| store | An add-on that holds content-addressed objects: a local directory, a NAS path, S3, others. |
| true source | The store that holds the authoritative copy of a dataset's content; working copies fetch from it. |
| add-on | A separately installed component that implements one stradler protocol (section 6). |
| agent workspace | An area reserved for an AI agent harness: its instructions, memory and logs. |
