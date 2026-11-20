# stradler add-on protocols: scheduler and container runtime

Status: draft (2026-11-03). Terms are defined in `GLOSSARY.md`; a term used here that the glossary does not define is
defined in section 1.1 and proposed for addition. Section numbers in parentheses such as "design 6" refer to the
stradler design document. Section numbers such as "WORKFLOWS 9.2" refer to `WORKFLOWS.md`.

Each rule carries its origin:

- **design**: stated by the stradler design, or cited as design in `WORKFLOWS.md` or `SCHEMAS.md`;
- **proposed**: needed to make the rule implementable; the design leaves it open. Proposed items are collected in
  section 7.

This document specifies two add-on protocols, the scheduler protocol and the container runtime protocol, and the rules
that connect them to the workflow record. It does not specify any add-on's implementation. A Slurm scheduler add-on
and an Apptainer container runtime add-on are the first expected implementations; they are named as examples only.

## 1. Transport and discovery

### 1.1 Terms used here

| Term | Meaning | Origin |
|---|---|---|
| add-on request | One JSON document the core writes to the add-on's standard input. | proposed |
| add-on response | One JSON document the add-on writes to its standard output. | proposed |
| operation | A named request kind: `capabilities`, `submit`, `status`, `cancel`, `build`, `inspect`, `wrap`. | proposed |
| protocol version | The `MAJOR.MINOR` version of this protocol specification: 1.0. | proposed |
| dependency | A relation from a job to earlier jobs of the same run, of kind `success` or `end` (2.3). | proposed |
| finalize job | The job that writes a run's final status (section 3). | proposed |
| bind | A host path made visible inside a container, with a target path and a mode (4.5). | proposed |

### 1.2 Process boundary

- [design] Each operation is one process of a command-line program: one JSON request on standard input, one JSON
  response on standard output (design 6).
- [proposed] The core starts the program named by `command` in `[addons.<name>]` (SCHEMAS 6), with no command-line
  arguments, writes the request, closes standard input, reads the response and waits for the exit.
- [proposed] No shell is involved on either side. The request carries every value; an argument is never built by
  joining strings for a shell.
- [proposed] Standard error is free text. The core keeps it in the run's log directory and never parses it.

### 1.3 Request envelope

- [proposed] Every request is one JSON object:

```json
{
  "protocol_version": "1.0",
  "kind": "scheduler",
  "op": "submit",
  "settings": {"partition": "example"},
  "params": {}
}
```

- `kind` is the configured kind of the add-on (SCHEMAS 6: `scheduler` or `container_runtime`).
- `settings` is the `settings` table of `[addons.<name>]` (SCHEMAS 6.2), copied verbatim. It must not hold credentials:
  the core never writes it to a record, and an add-on reads its credentials from its own environment.
- `params` holds the operation's parameters (sections 2 and 4).

### 1.4 Response envelope and error shape

- [proposed] A success:

```json
{"protocol_version": "1.0", "ok": true, "result": {}}
```

- [proposed] A failure:

```json
{"protocol_version": "1.0", "ok": false,
 "error": {"code": "backend_error", "message": "text for a person", "details": {}}}
```

- [proposed] The error codes form a closed set:

| Code | Meaning |
|---|---|
| `invalid_request` | The request is not valid JSON, does not match the envelope, or holds an unknown key in `params`. |
| `unsupported_version` | The request's `protocol_version` has a different major number (1.0). |
| `unsupported_operation` | The add-on does not implement the operation named in `op`. |
| `refused` | The add-on refuses the request on policy or capability grounds (for example a bind outside the allowed paths). |
| `not_found` | A job id, image or path named in the request does not exist. |
| `backend_error` | The native tool (scheduler command, runtime command) failed or returned an unmapped answer. |
| `internal` | A bug or an unexpected condition in the add-on. |

### 1.5 Exit codes

- [proposed] `0`: `ok` is true. `1`: `ok` is false and an `error` object is present. `2`: the request could not be parsed
  or does not match the envelope. `3`: the protocol major version is not supported. `4`: internal failure; the response
  may be missing.
- [proposed] Standard output holds exactly one JSON document and nothing else.
- [proposed] The core treats an exit code that disagrees with `ok`, unparseable standard output, or a missing response
  as an `internal` failure. The operation is then not treated as done. For `submit`, the core applies section 2.4
  before it retries.

### 1.6 Versioning

- [proposed] `protocol_version` is `MAJOR.MINOR`. This specification is version 1.0. The core sends the version it
  speaks. An add-on whose major number differs answers `unsupported_version` with exit code 3.
- [proposed] A minor increment adds optional keys only. A reader ignores unknown keys in a response.
- [proposed] An add-on refuses an unknown key in `params` with `invalid_request`, so that a misspelled key is never
  silently ignored. The core sends a parameter only when `capabilities` reports the feature it needs (2.2, 4.2).
- [proposed] The add-on's own `version` (SCHEMAS 6: `MAJOR.MINOR.PATCH`) is separate from the protocol version.

### 1.7 Discovery

- [design] The configuration names each add-on in the `[addons.<name>]` table of `config.toml` (design 3.3), with its
  `kind` and `version` (design 6; SCHEMAS 6).
- [proposed] The `command` key names the program that implements the protocol: one executable, no arguments (SCHEMAS 6).
- [proposed] The core calls `capabilities` before any other operation of a plan. It refuses an add-on whose reported
  `kind` or `version` differs from the configured one (section 5).
- [design] A local scheduler and a no-container runtime ship in the core, so that the tool runs with no add-on installed
  (design 6, core default).
- [proposed] A built-in add-on needs no `[addons]` entry and does not use this protocol (SCHEMAS 8, item 13). The
  container name `none` stays reserved for the no-container runtime (WORKFLOWS 12, item 5).

## 2. Scheduler protocol

### 2.1 Operations

- [design] A scheduler add-on implements `capabilities`, `submit`, `status` and `cancel` (design 6).
- [proposed] No other scheduler operation exists in this protocol version.

### 2.2 `capabilities` result

- [proposed] The result is one object:

| Key | Type | Meaning |
|---|---|---|
| `kind` | `"scheduler"` | Must equal the configured kind. |
| `version` | string | The add-on's version. |
| `non_blocking` | boolean | `true` when `submit` returns without waiting for the job to run. A non-blocking scheduler needs a finalize job (section 3). |
| `dependency_kinds` | list, subset of `success`, `end` | Dependency kinds the add-on implements. |
| `array_jobs` | boolean | Whether one job can hold several tasks (an array). |
| `resources` | list, subset of `cpus`, `memory`, `time`, `gpus` | Resource keys the add-on can request. |
| `platforms` | list of strings | Platform ids the add-on runs on (section 5). |
| `container_image_formats` | list of strings | Image formats a job of this scheduler can run from a container (section 5.2, `incompatible.pairing`). An empty list means the scheduler runs no container jobs. |
| `job_name_max` | integer | Maximum length of a job name. |
| `states` | list | The state names of section 2.5, exactly. |

### 2.3 `submit` parameters

- [design] The operation is `submit(jobs, dependencies, resources) -> job ids` (design 6).
- [proposed] Placement of the parameters (P11): the request holds `jobs` and `submission_key`; each job of `jobs` carries
  its own `dependencies` and `resources`, so that the values of one job travel with it. The names match design 6.
- [proposed] `submission_key`: a string of the grammar `[A-Za-z0-9_-]+`, unique per run and per submission. It is used
  for retries (2.4).
- [proposed] `jobs`: an ordered list. Each job is an object:
  - `name`: a string of the grammar `[A-Za-z0-9_-]+`, at most `job_name_max` characters.
  - `argv`: a list of strings. This is the job body. The add-on runs it as an argument vector, never through a shell.
  - `cwd`: an absolute path, the working directory of every task.
  - `array`: an integer of at least 1, or `null`. With an integer, the add-on runs that many tasks with indices
    `0` to `array - 1`. Each task sets the environment variable `STRADLER_TASK_INDEX` to its index and runs `argv`
    unchanged.
  - `resources`: an object with the values the core normalized: `cpus` (integer, at least 1), `memory` (a number
    followed by `K`, `M`, `G` or `T`, WORKFLOWS 3), `time` (`[D-]HH:MM:SS`), `gpus` (integer, at least 0). A key the
    add-on does not list in `resources` (2.2) is refused.
  - `log_dir`: an absolute path. The add-on writes the standard output and error of each task to
    `<log_dir>/<name>-<index>.out` and `.err`, with the index omitted for a job without `array`. It writes nothing else
    there.
  - `dependencies`: a list of `{"on": "success" | "end", "jobs": [job id, ...]}`. Each job id must have been returned
    by an earlier `submit` (this or a previous call). An unknown id is refused.
- [proposed] `submit` is all or nothing. If one job of the call cannot be submitted, the add-on cancels the jobs it
  already submitted in this call and returns `backend_error`.

### 2.4 `submit` result, retries and idempotency

- [proposed] The result is `{"jobs": [{"name": ..., "job_id": ..., "array_size": ...}]}`, in request order. `job_id` of
  an array job is the base id; the task ids are `<job_id>_<index>` in `status` results (2.5). `array_size` is the number
  of tasks, or `null`.
- [proposed] A `submit` may end without a response (exit code 4, or a killed process). The core then retries with the
  same `submission_key`. For a key the add-on has already completed, it returns the earlier result and submits nothing.
- [proposed] The add-on states how it finds earlier submissions (for a Slurm add-on, a job comment that carries the
  key, looked up before submitting). If the add-on cannot tell whether a key was submitted, the core does not retry; it
  records the error in the run and does not submit a duplicate.

### 2.5 `status` and the state set

- [design] `status` takes a list of job ids (design 6, `status(ids)`).
- [proposed] The request key is `job_ids`. Its result is
  `{"states": [{"job_id": ..., "task": ..., "state": ..., "exit_code": ..., "reason": ...}]}`. `task` is the array
  index, or `null` for a job without tasks. `exit_code` and `reason` are `null` when not known.
- [proposed] The state set is closed:

| State | Meaning |
|---|---|
| `pending` | Accepted by the scheduler, not started. |
| `running` | Started, not ended. |
| `completed` | Ended with exit code 0. |
| `failed` | Ended with a non-zero exit code, or was killed, or ran out of time or memory. |
| `cancelled` | Cancelled by a request, or by a dependency that can no longer be satisfied. |
| `lost` | The scheduler has no record of the job id. |

- [proposed] Every native state the scheduler reports maps to one of these states. A native state with no mapping is a
  `backend_error`; the add-on never guesses. For `completed` and `failed`, `exit_code` is the value the job body
  reported.

### 2.6 `cancel`

- [design] `cancel` takes a list of job ids (design 6, `cancel(ids)`).
- [proposed] The request key is `job_ids`. The result is `{"states": [...]}` in the shape of 2.5, after the cancel
  request.
  Cancelling an ended job is not an error. The core writes the cancel marker of section 3.5 before it calls `cancel`.

### 2.7 Mapping segments to jobs

- [proposed] Steps run in segments, and each segment is a barrier between the chains of its level (WORKFLOWS 5.2).
- [proposed] One job per segment that has at least one chain with tasks to run. The job is an array with one task per
  chain (WORKFLOWS 5.2, item 3). Each task runs the tasks of its chain in order and stops at the first failure
  (WORKFLOWS 5.2, item 5). The job body is the core's; the add-on does not know about tasks.
- [proposed] A segment with no runnable chain has no job. Its `job_id` in `run.json` is `null` (WORKFLOWS 9.2), and the
  next segment depends on the nearest earlier job of the run, or on none.
- [proposed] The core builds job names from the project's job-name prefix, the workflow family and the step name, with
  the unit added for a single-task job. The add-on checks the grammar and the length (2.2) and does not rename.
- [proposed] A single-task submission is one job with `array: null`.
- [proposed] A segment depends on the previous submitted job with `on: "end"`. The barrier waits for every chain of the
  previous segment to end, whether it succeeded or failed (WORKFLOWS 5.2, item 4); the reason for not using `success`
  is in section 7, item P2 (decided provisionally).
- [proposed] `run.json` `segments` entries take `job_name` (the name), `job_id` (the base id) and `array_size` (WORKFLOWS
  9.2). `backend` holds the scheduler add-on's name, and `backend_version` its version (optional key in record version 1).

## 3. Run finalization

### 3.1 Rule

- [design] The core writes run records and task states (design 4).
- [proposed] A run submitted to a scheduler whose `non_blocking` is `true` ends with exactly one finalize job F. F has a
  dependency `on: "end"` on every job the run submitted: every segment job and every single-task job. Because `end`
  is satisfied by every ending outcome, F runs after success, failure, cancellation and loss of any job.
- [proposed] The add-on must satisfy an `end` dependency on a job that has already ended immediately, so that F does
  not wait forever on a job the scheduler has already finished.
- [proposed] Origin: finding fnd-20261103T143505520415Z-6b566c48, which the requirement for this item cites. A run must
  be complete on disk without a reader.

### 3.2 Submission of F

- [proposed] F is submitted last, in its own `submit` call with its own `submission_key`, with dependencies on the job
  ids of every earlier job of the run.
- [proposed] `argv` of F is the core's finalize entry point with the run directory as argument. Its resources are the
  core's minimal choice. The add-on runs F like any job.

### 3.3 What F does

- [proposed] F performs these steps, in order:
  1. Reads every `task.json` of the plan. A planned task with no `task.json` counts as `failed`, reason
     `exited before record` (WORKFLOWS 7.3), and is listed in `missing_tasks`. F writes no task record.
  2. Derives the run status by the run status rule (WORKFLOWS 9.2): `interrupted` when the cancel marker of 3.5 exists;
     `failed` when a task is `failed` or `not-run`, or a task is missing; `ok` otherwise.
  3. Rewrites `run.json` only while its `status` is `running`. It sets `status`, `ended` (the latest task end, or F's
     own time when no task ended), `exit_code` (`0` for `ok`, `1` otherwise) and `finalize_job_id` (F's job id, from
     the environment variable `STRADLER_JOB_ID` that the add-on sets for every job). It writes through a temporary file
     and a rename (WORKFLOWS 9.1).
  4. Exits with code 0 after a successful write, and with a non-zero code only when the write failed.
- [proposed] The state of F in the scheduler reports only F. The run's outcome is in `run.json`.

### 3.4 Record fields

- [proposed] `run.json` (WORKFLOWS 9.2) keeps `status`, `ended` and `exit_code` as they are. It gains two keys:
  - `finalize_job_id`: string; present for a run on a non-blocking scheduler (optional key in record version 1);
  - `missing_tasks`: list of `{"step", "unit"}`; present when not empty.

### 3.5 Cancellation

- [proposed] Before it calls `cancel`, the core writes an empty marker file `<run directory>/.cancelled`. F reads the
  marker in 3.3. Cancelled tasks get no record from F; the run status is `interrupted` (WORKFLOWS 7.3).

### 3.6 Fallback reader

- [proposed] A reader that finds a run with `status: running` whose jobs have all ended, according to `status`, may run
  the same finalization function as F. The function is idempotent: a run whose status is no longer `running` is left
  alone. Reading by job status is a fallback only; a run must not depend on it.

### 3.7 Failure to submit F

- [proposed] If the submission of F fails, the core records the error in the run's log, leaves `run.json` at `running`,
  and retries F with the same key. Until F runs, section 3.6 applies.

### 3.8 Blocking schedulers

- [proposed] A scheduler with `non_blocking: false` (the built-in local scheduler, and any add-on that waits) has no
  finalize job. The core writes the final status itself when the run's last task ends (WORKFLOWS 9.2).

## 4. Container runtime protocol

### 4.1 Operations

- [design] A container runtime add-on implements `capabilities`, `build`, `inspect` and `wrap` (design 6).
- [proposed] No other container runtime operation exists in this protocol version.

### 4.2 `capabilities` result

- [design] The result names the recipe formats the add-on builds and the image formats it runs (design 6).
- [proposed] The result is one object:

| Key | Type | Meaning |
|---|---|---|
| `kind` | `"container_runtime"` | Must equal the configured kind. |
| `version` | string | The add-on's version. |
| `recipe_formats` | list of strings | Container recipe formats the add-on builds, for example `apptainer-def`, `dockerfile`. |
| `image_formats` | list of strings | Image formats the add-on runs, for example `sif`. |
| `platforms` | list of strings | Platform ids the add-on runs on (section 5). |
| `digest_algorithms` | list of strings | Must include `sha256`. |
| `bind_modes` | list, subset of `ro`, `rw` | Bind modes the add-on supports. |
| `build_privileges` | list, subset of `default`, `fakeroot`, `ignore-fakeroot` | Values of the `build` parameter `privilege` the add-on supports (4.3). |
| `build_offline` | boolean | Whether `build` needs no network access. |

### 4.3 `build`

- [design] The operation is `build(recipe) -> image + digest` (design 6).
- [proposed] Parameters: `recipe` (an absolute path to the recipe file, read only), `format` (one of `recipe_formats`),
  `output` (an absolute path; the only file the add-on writes), and the optional `privilege`.
- [proposed] `privilege` states how the build gets the rights it needs. Values: `default` (no privilege option; the
  value when `privilege` is absent), `fakeroot` and `ignore-fakeroot`. The add-on lists the values it supports in the
  capability `build_privileges`. A value outside that list is refused with `refused`. The Apptainer add-on maps
  `default` to no flag, `fakeroot` to `--fakeroot` and `ignore-fakeroot` to `--ignore-fakeroot-command`.
- [design] The result carries the digest with its format (design 6, "digests carry their format").
- [proposed] Result: `{"image": <output>, "format": <image format>, "digest": "sha256:<64 lowercase hexadecimal digits>"}`
  (WORKFLOWS 10.2).
- [proposed] `build` runs inside the call. When a build needs compute, the core submits the build call as a scheduler
  job, like any other compute (P8).

### 4.4 `inspect`

- [design] The operation is `inspect(image) -> digest, labels` (design 6). The digest carries its format (design 6).
- [proposed] Parameter: `image`, an absolute path. Result:
  `{"format": ..., "digest": "sha256:...", "size_bytes": ..., "labels": {...}}`, where `labels` is an object of string
  values (possibly empty) that the image declares.
- [proposed] The digest is the SHA-256 of the bytes of the image file. A directory image has no digest in this version;
  `inspect` refuses it with `refused` (section 7, item P7).

### 4.4.1 Docker image format

- [proposed] Format `docker`. A container of this format names an image reference
  `docker://<name>@sha256:<64 lowercase hex>`. A reference without the digest (tag only) is refused with `refused`; the
  message names the container.
- [proposed] `inspect` of such a reference returns `format` `docker` and `digest` = the reference's sha256, without
  network access; `size_bytes` is `null` and `labels` is empty.
- [proposed] `wrap` returns the runtime's argument vector running the reference directly, with the bind handling of 4.5.
- [proposed] `build` with recipe format `docker` takes the reference as `recipe` and pulls it to a SIF at `output`. The
  result is `{"image", "format": "sif", "digest": <SIF sha256>, "pin_digest": <reference sha256>,
  "resolved_digest": <SIF sha256>}`. The record keeps the docker digest as the pin and the SIF sha256 as the resolved
  digest.

| Format row | Kind | Operations | Pin |
|---|---|---|---|
| `sif` | image | `inspect`, `wrap` | sha256 of the file |
| `docker` | image and recipe | `inspect`, `wrap`, `build` | sha256 in the reference (digest-pinned only) |

### 4.5 `wrap`

- [design] The operation is `wrap(argv, binds, image) -> argv` (design 6).
- [proposed] Parameters: `argv` (a list), `image` (an absolute path), `binds` (a list of
  `{"source": <absolute host path>, "target": <absolute path inside the container>, "mode": "ro" | "rw"}`), and
  `workdir` (a path inside the container, or `null`; not in design 6). Result: `{"argv": [...]}`.
- [proposed] `wrap` starts nothing. The returned argument vector is run by the job body, never through a shell.
- [proposed] The add-on adds no bind of its own. Host mounts the runtime would add by default are disabled (section 7,
  item P9). Every bind comes from `binds`.
- [proposed] Each `source` is absolute, contains no `..` segment, and is refused otherwise. A mode outside `bind_modes`
  is refused.
- [design] The areas of a project are declared in `structure.toml` (design 3.3).
- [proposed] The core passes only paths inside the declared project areas. The core checks that before it calls `wrap`.

### 4.6 Digest pin check

- [proposed] The core, not the add-on, performs the pin check (WORKFLOWS 9.4). For a task that runs from an image:
  1. the core calls `inspect` on the image and takes its `digest` as `resolved_digest`;
  2. `pin_digest` is the recipe's `pin.sha256` in the `sha256:` form;
  3. when the pin is the all-zero placeholder, the record gets `pin_placeholder: true` and no comparison is made;
  4. otherwise `pin_matches` is the result of comparing `resolved_digest` with `pin_digest`.
- [proposed] A mismatch is recorded and does not fail the task (WORKFLOWS 9.4).

### 4.7 What `task.json` records

- [proposed] The container object of WORKFLOWS 9.4 is recorded: `name`, `kind`, `pin_digest`, `resolved_digest`,
  `pin_matches`, and `pin_placeholder` when true.
- [design] Records store format and digest together (design 6). For an image container, the container object records
  `format`, the image format from `inspect`, next to the digests of the bullet above.
- [proposed] The key is written by this protocol's writers in record version 1. A reader that meets a container object
  without `format` reads the format as unknown; it does not fail (WORKFLOWS 9.5: readers ignore unknown keys).

## 5. Compatibility check at plan time

### 5.1 Order

- [design] At plan time the core checks the pairing of scheduler and runtime, and an incompatible pairing is refused
  before anything runs (design 6). The platform check is design 6 as well: the plan refuses a recipe whose requirements
  the platform cannot meet.
- [proposed] The check runs after the recipe and the structure are read and before the plan is returned:
  1. the core calls `capabilities` on each add-on the plan needs: the scheduler when the plan has segments and the
     scheduler is not the built-in local one, and each runtime used by a container other than `none`;
  2. the core checks the rules of 5.2 against the plan and the platform the core runs on;
  3. on a failure, the core refuses the plan with the message of 5.3 and writes nothing.
- [proposed] The check reads capabilities and, for an `image` container, may read the image with `inspect`. It runs
  nothing and submits nothing (WORKFLOWS 5.1: building a plan writes nothing).

### 5.2 Rules and refusal codes

- [proposed] Each rule has one refusal code. The codes `incompatible.pairing` and `incompatible.platform` are design 6;
  the other codes are proposed.

| Code | Rule |
|---|---|
| `incompatible.kind` | `capabilities.kind` differs from the configured kind. |
| `incompatible.version` | `capabilities.version` differs from the configured version. |
| `incompatible.protocol` | The add-on's protocol major number differs (1.0). |
| `incompatible.missing_add_on` | The plan needs an add-on with no `[addons]` entry (built-in add-ons excepted). |
| `incompatible.dependency` | A segment boundary needs a dependency kind not in `dependency_kinds`. |
| `incompatible.array` | A segment has more than one task and `array_jobs` is `false`. |
| `incompatible.resource` | A step sets a resource key not in `resources`. |
| `incompatible.name` | A job name is longer than `job_name_max`. |
| `incompatible.recipe_format` | A container's recipe format is not in `recipe_formats`. |
| `incompatible.image_format` | An `image` container's format is not in `image_formats`. |
| `incompatible.pairing` | A container job's image format is not in the scheduler's `container_image_formats` (2.2). Example: a scheduler whose `container_image_formats` is `["oci"]` is given a `sif` image. |
| `incompatible.digest` | `digest_algorithms` does not include `sha256`. |
| `incompatible.bind_mode` | The plan needs a bind mode not in `bind_modes`. |
| `incompatible.platform` | The recipe declares `platforms` (design 6) and the core's platform is not in it, or the core's platform is not in an add-on's `platforms`. |

### 5.3 Refusal message

- [proposed] A refusal reads:
  `stradler: plan refused: <code>: <kind> "<add-on name>" <version> <what is missing>; <step or container>: <what it needs>.`
- [proposed] Examples:
  - `stradler: plan refused: incompatible.dependency: scheduler "example-sched" 0.1.0 does not support dependency kind "end" needed by segment 2 (step "count").`
  - `stradler: plan refused: incompatible.recipe_format: container_runtime "example-runtime" 0.1.0 does not build recipe format "dockerfile" needed by container "tools".`
- [proposed] A message names the add-on, the missing capability and the step or container. It never prints a value from
  `settings`.

## 7. Proposed items

Each item is a proposed rule. Items P1 and P3 to P12 need a decision or a review at the item that implements the add-on.
P2 is decided provisionally.

- **P1.** Finalize job for non-blocking schedulers (3.1 to 3.7). Origin: the finding named in 3.1.
- **P2.** Segment barrier as `on: "end"`, not `afterok` (2.7). Decided provisionally on
  2026-11-03; the rule is written as `on: "end"`. Reason: WORKFLOWS 5.2, item 4, starts the next segment only after every
  chain of the previous segment has ended, whether it succeeded or failed. The current Slurm backend uses `afterok`,
  which cancels a downstream job when an upstream job fails, and then no task record is written for it; WORKFLOWS 5.2,
  item 5, requires `not-run` for the following tasks. A fail-fast barrier that writes `not-run` records for cancelled
  tasks was considered and not chosen: F would then write task records, which reverses 3.3, step 1.
- **P3.** Key names of the record fields: `format` in the container object (4.7; the recording is design 6, the key name
  is proposed); `backend_version` in `run.json` (2.7); `finalize_job_id` and `missing_tasks` in `run.json` (3.4).
- **P4.** `submission_key` and the retry rule (2.3, 2.4). Each add-on states its lookup method.
- **P5.** The closed state set and its mapping rule (2.5).
- **P6.** The environment variables `STRADLER_TASK_INDEX` (2.3) and `STRADLER_JOB_ID` (3.3).
- **P7.** Directory images have no digest in this version (4.4).
- **P8.** `build` runs inside the call; a build that needs compute is submitted as a job (4.3).
- **P9.** Host mounts that a runtime adds by default are disabled; every bind comes from `binds` (4.5).
- **P10.** Design-origin tags checked against design draft 3 (2026-10-29), sections 6, 3.3 and 11.
- **P11.** Placement of `dependencies` and `resources` inside each job of `submit` (2.3). Design 6 lists them as
  parameters of `submit`; this version keeps the names and places them per job.
- **P12.** The scheduler capability `container_image_formats` (2.2) and the refusal `incompatible.pairing` (5.2). The
  pairing check is design 6; the key is the proposed way to state the scheduler's side of it.
- **P13 (open).** The refusal `incompatible.recipe_format` (5.2) is not implemented in this version: the model has no
  source of a container's recipe format yet, so the core cannot compare it with `recipe_formats`.
- **P14.** Jobs use the local filesystem. A custom `fs` passed to the run is not seen by the jobs a scheduler add-on
  starts (2.7).
- **P15.** The `command` of an add-on is one executable and takes no arguments (1.7); the core refuses a command that
  contains whitespace with `invalid_config`.

## 8. Store protocol: the `digest` operation

Add-ons of kind `store` or `remote` speak the envelope of section 1. This section defines one optional operation,
used by `stradler verify --replicas` (`SNAPSHOTS.md` section 7.6) to check a copy that the core cannot read itself.

| Operation | Required | Purpose |
|---|---|---|
| `digest` | no | Return the size and sha256 of files in the store, without sending their content. |

`digest` request: `kind` is the add-on's kind, `settings` as in section 1.3 (the add-on's `settings` from
`config.toml`, with the `settings` of `[addons.<name>]` in `local.toml` laid over them), and

| `params` key | Type | Meaning |
|---|---|---|
| `store` | string | Logical store name from `[stores]`. |
| `store_settings` | object | The `[stores.<name>]` table of `local.toml`, or `{}`. |
| `paths` | array of strings | Paths relative to the store root (`<dataset>/<entry path>`, POSIX). At most 500 per request. |

`digest` result: `{"files": [{"path": <string, as requested>, "size": <integer bytes>, "sha256": <64 hex digits>}]}`.
A path at which no regular file lies is omitted; the core counts it as `missing`. A `sha256` may carry the prefix
`sha256:`.

An add-on that does not implement `digest` answers `unsupported_operation`; the core then reports "verify not
supported" for that store. Any other error (for example `backend_error`) means the store is `unreachable`. Local
stores (a `path` in `local.toml`) are read by the core and never call this operation.
