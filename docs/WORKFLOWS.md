# stradler workflow specification

Status: draft (2026-10-30). Terms are defined in `GLOSSARY.md`; a term used here that the glossary does not define is
defined in section 2 and proposed for addition (section 12). Section numbers in parentheses such as "design 4" refer
to the stradler design document.

Each rule carries its origin:

- **design**: stated by the stradler design;
- **proposed**: needed to make the rule implementable; the design leaves it open. Proposed items are collected in
  section 12.

## 1. Scope and conventions

This document specifies:

- the workflow recipe format (section 3);
- units and levels as the planner sees them (section 4);
- the plan, segments, chains and binding rules (section 5);
- the join (section 6);
- the task and its states (section 7);
- the current-task rule (section 8);
- the run record and task record (section 9);
- canonical JSON and digests, which every implementation must reproduce exactly (section 10);
- the conformance data layout shared by all implementations (section 11).

Out of scope: the export of outputs as sealed datasets, the data handler, the JSON Schema file of the recipe, and the
Python and Rust APIs. The scheduler and container runtime protocols (design 6) are specified in `ADDONS.md`; the run
record fields they write are the ones of section 9.

Conventions (proposed):

- Text files are UTF-8 with LF line endings.
- Paths are POSIX paths relative to the project root. An absolute path, a `..` segment or a path inside `.stradler/`
  is refused.
- Timestamps are UTC in the form `YYYY-MM-DDTHH:MM:SSZ` (second precision).
- Identifiers match `[A-Za-z0-9_-]+` unless a rule states otherwise.
- Type names: *string*; *integer* (base-10 digits with an optional leading `-`); *boolean* (`true` or `false`);
  *mapping* (a set of named members); *list* (ordered); *digest* (section 10.2).

## 2. Terms used here

| Term | Meaning | Origin |
|---|---|---|
| unit | The set of files one task processes at one level, identified by a unit identifier. | proposed |
| unit identifier | A string that names a unit, unique within its level. Built by the data handler; nested identifiers are joined with `/`. | proposed |
| level | A named position in the unit hierarchy. Levels are totally ordered from finest to coarsest. | proposed |
| segment | A maximal run of consecutive steps of the recipe that share one level. | proposed |
| chain | For one segment and one unit, the ordered list of tasks of that unit. | proposed |
| placeholder | `${name}` in a command, or `{name}` in a path template (section 3.8). | proposed |

The core knows two levels. `file` is the finest; `group` is the coarsest and holds exactly one unit, whose identifier
is `group`. A data handler declares the intermediate levels, ordered between them (section 4). A recipe may name only
levels that the handler of its dataset declares, plus `file` and `group`.

## 3. Workflow recipe

A workflow recipe is a declarative document that defines a workflow. It is read on every run (glossary).

### 3.1 File format

The recipe is one YAML document in the strict subset read by the built-in reader (design 3.3.2) (design):

- accepted: block mappings, block sequences, flow sequences whose elements are scalars, plain and quoted scalars, and
  comments introduced by a space and `#`;
- refused, with error code `yaml.unsupported`: flow mappings (`{...}`), anchors, aliases, tags, block scalars (`|`, `>`),
  directives and multi-document streams;
- refused, with error code `yaml.syntax`: tab indentation, duplicate keys, and any other text outside the subset.

Every scalar is a string. The schema converts the scalars of a field where the field says so (proposed):

- an integer field accepts a string of the form *integer* (section 1);
- a boolean field accepts only the strings `true` and `false`.

A recipe therefore uses block mappings for every mapping. A key with no value reads as the empty string, which the
schema refuses wherever a mapping or a list is required.

### 3.2 Top-level keys

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `schema` | string, value `workflow@1.0.0` | yes | proposed (same key as the project files) | Schema reference of this recipe. |
| `name` | string | yes | proposed | Workflow name. The part before the first `.` must equal `family`, followed by `.` and the rest of the name. |
| `family` | string, `[A-Za-z0-9_-]+` | yes | proposed | Group of related workflows. |
| `version` | string, `MAJOR.MINOR.PATCH` | yes | proposed | Version of the workflow. Each part is a decimal number without leading zeros. |
| `purpose` | non-empty string | yes | proposed | What the workflow is for. Copied into every task record. |
| `containers` | mapping of container name to container entry | no | proposed | Container entries (3.3). Absent when every step uses `none`. |
| `inputs` | mapping | yes | proposed | The single input block (3.4). |
| `steps` | non-empty list of step mappings | yes | proposed | The steps, in recipe order (3.5). |

Any other top-level key is refused with error code `key.unknown`.

### 3.3 Container entries

`containers` maps a container name to an entry. A container name matches `[A-Za-z0-9_-]+` and is not `none`, which is
reserved for "no container runtime" (proposed).

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `kind` | `recipe` or `image` | no, default `recipe` | proposed | `recipe`: the image is built from a container recipe. `image`: the image already exists and is referenced by name. |
| `recipe` | string (path) | yes when `kind` is `recipe` | proposed | Project-relative path of the container recipe file, inside an area of role `recipe` or `code` (design 3.1). |
| `image` | string | yes when `kind` is `image` | proposed | Reference of an image in an area of role `image`. |
| `pin` | mapping | yes | design (pins, 4) | The image the run must use. |
| `pin.version` | non-empty string | yes | proposed | Version label of the image; recorded, not checked. |
| `pin.sha256` | 64 hexadecimal digits | yes | design (digests carry their format, 6) | Digest of the image file. Written in lowercase; upper-case input is accepted. The all-zero value is a placeholder: the task is recorded with `pin_placeholder` and is not checked against the file. |
| `pin.path` | string (path) | no | proposed | Project-relative path of the pinned image file, when it lies in the project. |

No other keys are allowed in a container entry.

### 3.4 The `inputs` block

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `dataset` | non-empty string (path) | yes | proposed | Project-relative path of the dataset folder. The folder lies in an area. |
| `filter` | mapping of key to string or list of strings | no | proposed | Selection of files, read by the data handler. The default handler (section 4) reads two keys, `include` and `exclude`, each a list of glob patterns relative to the dataset folder. Other keys are refused by that handler. Absent: every file of the folder. |
| `units` | mapping of level name to mapping | no | proposed | Selection of units at intermediate levels. Each level maps to `include` and/or `exclude`, each a list of unit identifiers or `*`. An absent `include`, or `*` in it, selects every unit of the level. |

Every key of `units` must be a level that the handler of `dataset` declares.

### 3.5 Step entries

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `name` | string, `[A-Za-z0-9_-]+` | yes | proposed | Unique name of the step. It names record directories. |
| `purpose` | non-empty string | yes | proposed | Why the step exists. Copied into every task record. |
| `level` | string | yes | design (units, 4) | `file`, `group`, or an intermediate level declared by the handler. |
| `container` | string | yes | proposed | `none`, or a name in `containers`. |
| `command` | list of strings, at least one element | yes | design (command template) | Program and arguments of the task. Run as an argument vector, never through a shell (section 7.2). |
| `in` | list of step input entries | no | proposed | Inputs of the step (3.6). Absent: no inputs. |
| `out` | non-empty list of step output entries | yes | proposed | Outputs of the step (3.7). |
| `on_incomplete` | string, value `partial` | no | proposed | A join may proceed with fewer upstream units (section 6). Absent: strict. |
| `min_units` | integer, `>= 0` | no | proposed | Least number of upstream units a partial join needs. Default `1`. Allowed only with `on_incomplete`. |
| `resources` | mapping | no | proposed | Hints for a scheduler. Keys: `cpus` (integer, `>= 1`), `memory` (a number followed by `K`, `M`, `G` or `T`), `time` (`[D-]HH:MM:SS`), `gpus` (integer, `>= 0`). Other keys are refused. The built-in local scheduler records these hints and does not enforce them. |
| `interpretation` | non-empty string | no | proposed | How to read the results of the step. Copied into task records. |
| `code` | string | no | design (step codes, 4) | Step code (3.9). |
| `stage_kind` | one letter | no | proposed | Kind letter the code derives from (3.9). |

A key not listed here is refused with error code `key.unknown`.

### 3.6 Step input entries

Each entry of `in` binds one placeholder of the command.

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `as` | identifier, `[A-Za-z_][A-Za-z0-9_]*` | yes; not allowed in `else` | proposed | Placeholder name. Unique within the step and different from every output name of the step. |
| `from` | string | yes | proposed | `inputs`, the unit's own files; `fixed`, a path given in the recipe; `named`, a name given to the run; or the name of an earlier step of the recipe. |
| `path` | string (path) | yes when `from` is `fixed` | proposed | Project-relative path. It must exist at plan time, else the binding is missing. |
| `name` | identifier | yes when `from` is `named` | proposed | Key of the named inputs that a run receives (a mapping of name to project-relative path). |
| `select` | mapping | no | proposed | Only with `from` set to a step name (3.6.1). |
| `else` | step input entry without `as` and without `else` | no | proposed | Binding used when the `condition` of `select` is false. |

#### 3.6.1 `select`

| Key | Value | Origin | Meaning |
|---|---|---|---|
| `for_each` | a level | proposed | Aggregate over the children of the unit at this level, in identifier order. The referenced step must have this level, and this level must be finer than the step's own level. |
| `pick` | `first_<level>` | proposed | Take the first child at `<level>`, in identifier order. The level rule of `for_each` applies. |
| `condition` | mapping with key `exists` (the name of an earlier `as` of the same step, or an output name of the referenced step) | proposed | Keep the binding only when the named path exists on disk at plan time. A name that is an earlier `as` of the step reads the path bound there, whatever step or input supplied it (every path of a list must exist); otherwise it names an output of the referenced task. With `for_each` or `pick`, the condition applies to each child; alone, to the unit. |

`for_each` and `pick` are mutually exclusive. A `select` needs at least one of the three keys.

A binding from a step name without `select` takes the referenced task of the same unit. The bound value is the first
declared output of that task, in `out` order. The referenced step must have the same level as the step, and it must
precede the step in the recipe (section 3.11, `ref.order`).

### 3.7 Step output entries

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `name` | identifier | yes | proposed | Unique within the step. Usable as a command placeholder. |
| `path` | string (path template) | yes | proposed | Project-relative path of the output, with placeholders of section 3.8. It must lie inside an area of role `output` or `record` (design 3.1). |

### 3.8 Paths and placeholders

Path templates (proposed). Placeholders are substituted when the plan is built:

- `{workflow}`: the workflow `name`;
- `{code}`: the step code (an error when the step has none);
- `{unit}`: the unit identifier;
- `{<level>}`: the identifier of the unit's ancestor-or-self at that level, for each level the handler declares;
- `{stem}`: for a `file` unit, the file name up to its first `.`.

An unknown placeholder is refused with error code `placeholder.unknown`. There is no escaping in version 1.0.0.

Command placeholders (proposed):

- `${name}` is replaced by the bound value of an `in` entry, or by the rendered path of an output;
- when the step has exactly one output, `${output}` and `${output_dir}` both name that output's path;
- a list value is joined with `,`, so a path that contains a comma cannot be bound (section 12);
- a placeholder name that matches nothing makes the task `not-run` with a reason; it is not left in the argument vector;
- a `$` that does not start `${name}` is literal text;
- substitution replaces text inside one argument; it never adds or removes an argument.

Extension hooks (optional arguments of `build_plan`; with neither, behaviour is exactly as above):

- `placeholder_resolver(name, ctx) -> str | None` is called first for every placeholder, so it may override a
  built-in one by returning a value: `{name}` in an output path and `${name}` in a command. When a resolver is given, a command name may contain dots
  (`${a.b}`). `ctx` is a dict with `kind` (`output` or `command`), `step`, `unit`, and for `output` the `output` name,
  for `command` the `bindings` and `outputs`. A string result is substituted; `None` leaves the placeholder
  unresolved (`placeholder.unknown` for an output, `not-run` for a command).
- `known_placeholder(name) -> bool` (optional argument of `parse_recipe`, `load_recipe` and `validate_document`):
  a path or command placeholder for which it returns true is not reported as `placeholder.unknown`, and command
  names may contain dots. Pair it with a `placeholder_resolver` that resolves those names at plan time.
- `output_namer(step, unit, output_name, path) -> str` is called on every rendered output path, before the path
  safety check, and returns the path to use.

The hooks are generic: a distribution supplies its own placeholder vocabulary and output naming.

### 3.9 Step codes

A step code is an optional label (design 4). The core checks its format and uniqueness.

- A code matches `[1-9][A-HJ-NP-Z][0-9]{2}`: a stage digit, a kind letter, and a two-digit substep, for example `1P01`.
- A kind letter is one uppercase letter other than `I` and `O`.
- When any step declares `stage_kind`, every step must. Codes are then derived (proposed):
  - stages are numbered 1, 2, … in the order in which each kind first appears in the recipe;
  - the kinds of one stage must be contiguous in recipe order;
  - there are at most 9 stages;
  - the substep counter of a kind starts at `01` and counts the steps of that kind, at most 99.
- A declared `code` must equal the derived code. Codes are unique within a recipe.

### 3.10 Validation

Validation reports every problem, not only the first. Each problem has a code, a location (for example
`steps[2].out[0].name`) and a message. Version 1.0.0 has no warnings: a problem is an error and the recipe is refused.

| Code | Problem |
|---|---|
| `yaml.syntax` | Text outside the subset, or not parseable (3.1). |
| `yaml.unsupported` | A construct the subset refuses (3.1). |
| `key.unknown` | A key not defined for this mapping. |
| `key.missing` | A required key is absent. |
| `value.invalid` | A value of the wrong type or outside its allowed set. |
| `name.duplicate` | A step name, output name, placeholder, container name or code used twice. |
| `name.prefix` | `name` does not begin with `family` and `.`. |
| `ref.unknown` | A `from` step, a container or a `named` input name is not defined. |
| `ref.order` | A `from` step does not precede the referring step. |
| `ref.level` | A level mismatch: a binding to another level without `for_each` or `pick`, or a `for_each` / `pick` level that the referenced step does not have. |
| `placeholder.unknown` | A path or command placeholder is not defined (3.8). |
| `path.unsafe` | A path is absolute, contains `..`, or lies inside `.stradler/`. |
| `path.area` | An output path is not inside an area of role `output` or `record`. |
| `code.format` | A code does not match the format of 3.9. |
| `code.derive` | Codes cannot be derived from the `stage_kind` declarations. |

The codes are stable identifiers, so conformance cases compare them (section 11).

### 3.11 Example

A generic two-step recipe. Area `work` is declared in the project structure with role `output`.

```yaml
schema: workflow@1.0.0
name: example.summarize
family: example
version: 1.0.0
purpose: Count the lines of each text file, then add the counts.
containers:
  tools:
    kind: image
    image: tools-image
    pin:
      version: 1.0.0
      sha256: "0000000000000000000000000000000000000000000000000000000000000000"
inputs:
  dataset: texts
  filter:
    include:
      - "*.txt"
steps:
  - name: count
    purpose: Count the lines of one file.
    level: file
    container: none
    command:
      - wc
      - -l
      - ${source}
    in:
      - as: source
        from: inputs
    out:
      - name: counts
        path: work/{stem}.count
  - name: total
    purpose: Add up the counts of all files.
    level: group
    container: none
    command:
      - sum-counts
      - ${all_counts}
    in:
      - as: all_counts
        from: count
        select:
          for_each: file
    out:
      - name: total
        path: work/{workflow}-total.txt
```

The `total` step is a join (section 6): its `for_each: file` binding waits for every `count` task.

## 4. Units and levels

A data handler maps a dataset to units (design 6). Its contract is a later item; this section fixes what the planner
relies on.

- The levels are totally ordered: `file` is the finest, then the intermediate levels the handler declares in order
  from finest to coarsest, then `group`.
- Each unit has a level, a unit identifier, and a sorted list of the project-relative files it covers.
- The unit identifier is unique within its level.
- The children of a unit at a finer level are the units inside it, sorted by identifier. Identifiers are compared as
  UTF-8 bytes.
- The group unit has identifier `group` and covers every selected file.
- Discovery is deterministic: the same files in the same state give the same units in the same order.

The default handler shipped with the core (proposed) has no intermediate levels:

- each selected file is a `file` unit, whose identifier is its project-relative path and whose file is that path;
- one `group` unit, `group`, covers every selected file;
- it reads only the `filter` keys `include` and `exclude` (3.4).

## 5. Plan

### 5.1 Definition

The plan is the resolved list of tasks of a run, computed before anything executes (glossary). Building the plan reads
the recipe, the structure, the units, the run records and the files of the project. It writes nothing and runs
nothing. The same inputs give the same plan.

### 5.2 Segments, barriers and chains

1. Steps are taken in recipe order. A run may name a subset of steps; the subset keeps recipe order (proposed).
2. Consecutive steps with the same level form one segment. A change of level starts a new segment. The boundary
   between segments is a barrier.
3. For each segment and each unit of its level, the plan holds one chain: the tasks of that unit for the segment's
   steps, in recipe order.
4. The chains of one segment may run in any order or in parallel. The next segment starts only after every chain of
   the previous segment has ended, whether it succeeded or failed.
5. Within one chain, a task that is `failed` or `not-run` makes every following task of that chain `not-run` (reason:
   upstream task in this unit's own chain did not run).

### 5.3 Binding inputs

The planner evaluates the tasks of a chain in order. For each task:

1. Render the output paths. A rendering error makes the task `not-run`.
2. Set `outputs_exist` when `force` is not set and every rendered output exists.
3. Resolve each `in` entry in order:
   - `inputs`: the unit's files. One file gives one path; several give a list in sorted order.
   - `fixed`: the `path`. Missing on disk gives a missing binding.
   - `named`: the value of `name` in the named inputs. Absent gives a missing binding.
   - a step name, no `select`: the first output of that step's task for the same unit. A missing or `not-run` task gives
     a missing binding.
   - `for_each`: the values of the children, in identifier order, joined into one list. `included` counts children
     whose value resolved; `expected` counts all children.
   - `pick`: the value of the first child in identifier order.
   - `condition`: the value only when the `exists` path is present on disk (3.6.1); otherwise `else` if present, else no value.
4. A binding without a value is a hard failure of the task, unless the entry is a join (section 6).
5. Render the command: substitute placeholders (3.8). Unresolved placeholders make the task `not-run`.
6. Decide the status (5.4).

### 5.4 Task status at plan time

| Status | Meaning |
|---|---|
| `planned` | The task will run. The plan may carry a reason, for example why an existing output is stale. |
| `skip` | The task does not run; its declared outputs are current or complete. A `skipped` record is written. |
| `not-run` | The task cannot run; the reason is recorded. A `not-run` record is written. |

Decisions, in order (proposed):

- an output path that cannot be rendered: `not-run`;
- a hard failure of 5.3 and `outputs_exist`: `skip`, reason "declared outputs exist; inputs not resolvable, not checked";
- a hard failure of 5.3 without `outputs_exist`: `not-run`;
- an unresolved command placeholder: `not-run`;
- `outputs_exist` and the task is current (section 8): `skip`;
- `outputs_exist` and the task is not current: `planned` with a reason, unless an existing output lies in an area with
  mutability `sealed` or `immutable`, in which case `not-run` (reason: existing output in a sealed or immutable area;
  it is never removed);
- otherwise: `planned`.

### 5.5 The plan document

`plan.json` is written in the run directory before any task starts (section 9.1). It holds:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `segments` | list | yes | Segments in order. Each has `level` (string), `steps` (list of step names) and `chains` (list). |
| `chains` | list | yes, per segment | Each chain has `unit` (identifier) and `tasks` (list). |
| `tasks` | list | yes, per chain | Each task has `step`, `level`, `unit`, `status`, and, when present, `reason`, `argv`, `bindings`, `outputs`, `missing`, `included`, `expected`. |
| `bindings` | mapping of placeholder name to string or list of strings | optional | Bound values of the task. |
| `outputs` | mapping of output name to project-relative path | optional | Rendered outputs of the task. |
| `missing` | list of `{unit, step, reason}` | optional | Upstream units that a binding could not resolve. |
| `named_inputs` | mapping of name to project-relative path | optional | The named inputs the run received. |
| `non_loop_inputs` | list | optional | Entries as in section 9.2. |

Optional keys are absent when their value would be empty. Lists keep their order; mappings are serialized with sorted
keys (section 10.1).

### 5.6 Extension points of `build_plan`

A distribution may pass optional callables to `build_plan`. The planner knows nothing of what they decide; without
them the behaviour of 5.1 to 5.5 is unchanged.

| Hook | Signature | When it is called | Effect |
|---|---|---|---|
| `placeholder_resolver` | `f(name, ctx) -> str or None` | Every placeholder, before the built-in rules (3.8). | The returned text replaces the placeholder; `None` falls back to the built-in rule. |
| `output_namer` | `f(step, unit, output_name, path) -> str` | Each rendered output path, before the safety check. | The returned path is used. |
| `task_filter` | `f(step, unit) -> bool`, `(bool, text)` or `"omit"` | Per step and unit, before anything is rendered or bound (also for a task whose chain is already dead). | `False` leaves the task out: status `not-run`, reason `text`, default `excluded by the task filter`. As for any `not-run` task, the rest of the chain is `not-run` (5.2). The string `"omit"` (or `("omit", text)`) removes the task from the plan: no task in the chain, the chain is not cut, and a step omitted for every unit of its segment is absent from `segment.steps`. A later step that reads an omitted step finds no task of it (see `input_resolver`). |
| `input_resolver` | `f(step, unit, input_name, entry) -> str, (str, {"exists": bool}) or None` | An `in` entry the planner does not resolve itself: a `from` that is neither a built-in kind nor a step, a `from` step that has no task in this plan (for example a step not in the run's subset), or a `named` input whose path is not a safe relative path. | The returned path is the bound value. It must exist on disk, else the binding is missing; return `(path, {"exists": False})` to skip that check. `None` keeps the planner behaviour. When the hook is given, an unsafe named path is not refused up front (3.6: it is passed to the hook; without a hook it is a `PlanError`). |
| `condition_resolver` | `f(step, unit, name) -> bool or None` | Each `select.condition.exists` (3.6.1), before the planner's own rule; `step` is the step being bound, `unit` the unit the condition applies to (the child with `for_each` or `pick`), `name` the condition's name. | A bool decides whether the condition holds, for example from an output of any step for the unit or its child units; `None` keeps the planner rule. When the condition is false the `else` entry is bound (below). |
| `record_reader` | `f(path) -> mapping or None` | Each `task.json` the current-task rule (8.3) considers, newest run first, instead of the built-in read and `record_version` check (9.5). | The returned mapping is used as the record, in the shape of 9.3 (a distribution may translate its own shape and version); `None` ignores that file. The reader owns the version check; without the hook a `record_version` above the supported one is a `PlanError`. |
| `task_hook` | `f(task) -> task or None` | Each task that reached command rendering, after binding, output naming and command rendering, before the input components (8.2) and the status decision (5.4). | The hook may add to `task.bindings` (values are bound paths and enter the input digest), put opaque metadata in `task.extra` (written under `extra` in the task of `plan.json`, absent when empty) or set a status other than `planned`. `None` keeps the task. The planner treats added keys as opaque. |
| `fs` | object with `exists(rel)`, `is_file(rel)`, `is_dir(rel)`, `list_dir(rel) -> list[str]`, `lexists(rel)`, `is_link(rel)`, `stat(rel) -> {"size", "mtime_ns"} or None` (directories: summed size, newest mtime) and `read_text(rel) -> str` (project-relative paths) | Every read of the project by the planner (output and input existence, the record area scan, record files, output stats of the current-task rule) and of `run_workflow` (declared outputs after a task, stale-output and link checks, output stats written to the record, task logs, dataset directory and link scan). Record and log writes stay local. | Default `LocalFS(root)` answers from the local disk and keeps the behaviour of 5.1 to 5.5; another object lets a distribution plan against a project whose files are not plain local files (a remote store, a staged checkout, a test double). `build_plan(..., fs=)` and `run_workflow(..., fs=)`. |

Rules that go with the hooks:

- An `else` entry is bound like any entry (5.3): its own `select` (`for_each`, `pick`, `condition`) applies. The unit it
  is bound for is the unit the failed condition was evaluated for (the child, with `for_each` or `pick` on the main entry).
- `input_resolver` is also asked for a `fixed` entry whose path is not on disk, and for a `named` entry whose name the
  run did not receive; with `(path, {"exists": False})` it can bind a path that is not there yet.

Hooks must be deterministic: the same inputs give the same plan (5.1).

Not defined: a `from` step with a finer level than the step, without `select`, is `ref.level` (3.10); a step reads a
finer level only through `for_each` or `pick`.

## 6. Join

A join is a step that waits for all tasks of its upstream step (glossary). In version 1.0.0 a step is a join when at
least one of its `in` entries has `select.for_each`. A join's chains are at its own level, which is coarser than the
level of its upstream step. The barrier of section 5.2 separates the two.

- **Strict** (default). The binding is complete when `included` equals `expected`. Otherwise the task is `not-run`,
  reason `strict barrier, X/Y upstream units available`.
- **Partial** (`on_incomplete: partial`). The binding proceeds when `included` is at least `min_units` (default `1`).
  Otherwise the task is `not-run`, reason `partial aggregation below min_units (X/M)`.
- **Run-time recount.** Before a planned join task starts, the runner counts the upstream units whose task ended `ok`
  or `skipped`. The rule above is applied to that count. A shortfall makes the task `not-run`, reason
  `barrier: only X/Y upstream units succeeded`, with `included` and `expected` written to its record.

A join that is `not-run` makes the rest of its chain `not-run` (section 5.2).

## 7. Task

### 7.1 Identity and states

A task is one execution of a step for one unit of input (glossary). Its identity is the pair (step name, unit
identifier). Its record directory is `<run directory>/<step>/<unit>/`. A unit identifier that contains `/` gives nested
directories. An identifier with a `.` or `..` segment is refused.

A task record has one of four states (proposed):

| State | Meaning |
|---|---|
| `ok` | The command ran and exited with status 0. |
| `failed` | The command ran and did not exit with 0, was killed, or was cancelled; or the process ended without a record. |
| `skipped` | The plan decided `skip` (section 5.4). |
| `not-run` | The plan decided `not-run`, or a chain or a barrier prevented the task from running. |

The plan status `planned` is not a record state: a planned task has a record only after it has ended.

### 7.2 Execution

- The command runs as an argument vector, never through a shell, with the project root as working directory.
- Container `none`: the vector runs on the host. Any other container: the container runtime add-on wraps the vector (design 6).
- While the task runs, its record directory holds a file `.running` containing the UTC start time, and no `task.json`.
- `stdout.log` and `stderr.log` capture the output of the command.
- The exit code is the value the process reports. A process terminated by signal `N` has exit code `-N`.
- Before a `planned` task runs, every declared output that exists is removed, and its path is written to
  `removed_outputs` (proposed; only in areas of mutability `mutable`, section 5.4).
- After an `ok` exit, each declared output is checked. An output that does not exist makes the task `failed`, reason
  `declared output missing` (proposed). Output statistics are then measured (section 9.3).
- `task.json` is written when the process ends, and `.running` is removed after it.

### 7.3 Failure, cancellation and missing records

- A process that ends without writing `task.json` (for example, killed by the operating system) is recorded by its
  runner as `failed`, with its exit code and reason `exited before record`. Later tasks of the chain are `not-run`.
- Cancelling a run (proposed): a running task is recorded `failed` with reason `cancelled`; a task not yet started is
  recorded `not-run` with reason `run cancelled`; the run status is `interrupted` (section 9.2).
- Reproducing a task (proposed): a reproduction runs the task under a new run id with `force`. Its `task.json` carries
  `reproduces`, the run id and step/unit of the original record. The original record is never overwritten.

### 7.4 Warnings

A line of `stdout.log` or `stderr.log` that begins with `stradler-warning: ` is a warning (proposed). The text after the
prefix, trimmed, is recorded in `warnings`, unique, in order of appearance. A warning does not change the status.

## 8. Current-task rule

### 8.1 Definition

A task is current when a record shows the same step, inputs, parameters and image digests; current tasks are skipped
(glossary). This section makes the comparison exact.

The reference record is the `task.json` of the newest run (run ids sort chronologically) under the record area that
has the same step and unit identifier and status `ok`. A `skipped` record is not a reference.

### 8.2 Components

The record holds `input_components`, an object with four digests (proposed):

| Component | Digest of | Glossary term |
|---|---|---|
| `inputs` | the input digest of the bound values (section 10.3) | inputs |
| `command` | the canonical JSON of the planned argument vector (placeholders already substituted) | parameters |
| `container` | the UTF-8 text of `pin.sha256` as written in the recipe, lowercase; for `none`, the empty text | image digests |
| `workflow` | the canonical JSON of the array `[name, family, version]` | (recipe identity) |

`input_digest` is the digest of the canonical JSON of the four component digests, as a map.

### 8.3 Rule

A task is current when all of the following hold:

1. `force` is not set;
2. every declared output exists;
3. a reference record exists;
4. the four components of the plan equal those of the reference record;
5. for every declared output, the size and mtime of section 9.3 equal the recorded `output_stats`;
6. no bound input is an output path of a task that is `planned` in the same plan (upstream regenerated).

A task without a reference record is not current, and it is planned when its outputs exist (proposed; see section 12).

### 8.4 Reasons

When a task with existing outputs is not current, its reason names the first failed check, using these phrases:
`no ok record`, `inputs changed`, `parameters changed`, `container changed`, `workflow changed`,
`output changed since the recorded run`, `upstream output regenerated`.

## 9. Run record

### 9.1 Layout

The run record area is the area of role `record` (default path `records`, design 3.5). For each run:

```
<record area>/<run id>/
  plan.json                    written before the first task starts
  run.json                     written when the run starts (status running), rewritten when it ends
  <step>/<unit>/task.json      one per task of the plan, including skip and not-run
  <step>/<unit>/stdout.log
  <step>/<unit>/stderr.log
  <step>/<unit>/.running       present while the task runs
```

- A run id is `YYYYMMDDTHHMMSSZ-<8 lowercase hexadecimal digits>` in UTC (proposed). It sorts chronologically.
- A `skip` or `not-run` task gets its `task.json` when the run is built, with no log files.
- A record is written to a temporary name in its directory and renamed into place (proposed).
- Caches (for example a digest cache for container images) may live in the run directory. They are not part of the
  record and may be deleted.

### 9.2 `run.json`

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `record_version` | integer | yes | proposed | Shape version of this record: `1` for this document (9.5). |
| `run_id` | string | yes | proposed | The run id (9.1). |
| `workflow` | mapping | yes | proposed | See below. |
| `inputs` | mapping with `datasets` (list) | yes | proposed | Versions of the datasets the run read (below). |
| `backend` | string | yes | proposed | Scheduler that ran the tasks: `local` for the built-in scheduler. |
| `plan_digest` | digest | yes | design (run record: plan digest) | Digest of the canonical JSON of `plan.json` (10.4). |
| `stradler_version` | string | yes | design (framework version) | Version of stradler that wrote the record. |
| `stradler_revision` | string or null | yes | proposed | Source revision of stradler; null when unknown. |
| `non_loop_inputs` | list | yes (may be empty) | proposed | Named and fixed inputs the plan resolved (below). |
| `segments` | list | yes | proposed | Segment summaries (below). |
| `step_codes` | mapping of step name to code | only when a step has a code | proposed | Step codes of the steps that declare one. |
| `status` | string | yes | proposed | `running`, `ok`, `failed` or `interrupted` (below). |
| `started` | timestamp | yes | proposed | Start of the run. |
| `ended` | timestamp or null | yes | proposed | End of the run; null while `running`. |
| `exit_code` | integer | yes | proposed | `0` when `ok`; otherwise `1`. |

`workflow`:

| Key | Type | Meaning |
|---|---|---|
| `name`, `family`, `version` | string | As in the recipe. |
| `file` | string or null | Project-relative path of the recipe file; null when the recipe was built in memory. |
| `file_digest` | digest or null | Digest of the bytes of the recipe file. |
| `content_digest` | digest | Digest of the canonical JSON of the recipe document after schema conversion (10.4). |
| `inputs_digest` | digest | Digest of the canonical JSON of the `inputs` block. |

`inputs.datasets` holds one entry per dataset the run read:

| Key | Type | Meaning |
|---|---|---|
| `path` | string | Project-relative path of the dataset folder. |
| `role` | string | Role of the area that holds the dataset (`input`, `output`, …). |
| `revision` | string or null | Revision of the version control repository that holds the dataset (a commit, for git). Null when there is none (section 12). |
| `dirty` | boolean or null | Whether the repository has uncommitted changes under the dataset. |
| `reason` | string | Present when `revision` is null. |
| `repo` | string | Project-relative top folder of the repository, when it differs from the dataset folder. |
| `links` | integer | Number of symbolic links under the dataset; present when above zero. |
| `link_digest` | digest | Digest over the sorted pairs (path, link target); present with `links`. |

`non_loop_inputs` holds one entry per named or fixed input the plan resolved: `name`, `from` (`fixed` or `named`),
`relpath` (project-relative path), `digest` (section 10.3), and `unit` (the unit identifier, or null).

`segments` holds one entry per segment: `level`, `steps`, `job_name` (null for the local scheduler), `job_id` (null),
`array_size` (null).

Run status (proposed):

- `running`: the run has started and not ended;
- `interrupted`: the run was cancelled (7.3);
- `failed`: not cancelled, and at least one task is `failed` or `not-run`;
- `ok`: otherwise, that is, every task is `ok` or `skipped`.

### 9.3 `task.json`

| Key | Type | Required | Origin | Meaning |
|---|---|---|---|---|
| `record_version` | integer | yes | proposed | `1` (9.5). |
| `step`, `unit`, `level` | string | yes | proposed | Identity of the task (7.1) and its level. |
| `purpose` | string | yes | proposed | From the recipe. |
| `interpretation` | string | when the step has one | proposed | From the recipe. |
| `step_code` | string | when the step has a code | proposed | From the recipe. |
| `status` | `ok`, `failed`, `skipped`, `not-run` | yes | proposed | 7.1. |
| `argv` | list of strings | when planned to run | proposed | The argument vector as run. |
| `container` | container object or null | yes | proposed | 9.4; null for `none`. |
| `input_digest` | digest | when `argv` is present | proposed | Digest of the canonical JSON of `input_components`. |
| `input_components` | mapping of the four components (8.2) to digests | when `argv` is present | proposed | As in section 8.2. |
| `stradler_version`, `stradler_revision` | string or null | yes | proposed | As in 9.2. |
| `started`, `ended` | timestamp | when the command ran | proposed | Start and end of the command. |
| `exit_code` | integer | when the command ran | proposed | Section 7.2. |
| `reason` | string | when not `ok` | proposed | Why the task is not `ok`, or why it was planned. |
| `included`, `expected` | integer | joins only | proposed | Section 6. |
| `outputs` | mapping of output name to project-relative path | when the step has outputs | proposed | Rendered output paths. |
| `output_stats` | mapping of output name to `{size, mtime_ns}` | after an `ok` exit | proposed | Size in bytes and modification time in nanoseconds of each output. A directory has the summed size of its files and the newest mtime of itself and its contents. |
| `removed_outputs` | list of project-relative paths | when not empty | proposed | Outputs removed before the command ran (7.2). |
| `warnings` | list of strings | when not empty | proposed | Section 7.4. |
| `reproduces` | mapping `{run, step, unit}` | reproductions only | proposed | Section 7.3. |

### 9.4 Container object

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Container name from `containers`. |
| `kind` | `recipe` or `image` | yes | As in the recipe. |
| `pin_digest` | digest | yes | The recipe's `pin.sha256` as a digest (`sha256:` form). |
| `resolved_digest` | digest | when the image file was resolved | Digest of the image file the task ran from. |
| `pin_placeholder` | boolean | only when `true` | The pin is the all-zero placeholder. |
| `pin_matches` | boolean | when both digests are known and the pin is not a placeholder | Whether `resolved_digest` equals `pin_digest`. A mismatch is recorded; it does not fail the task. |

### 9.5 Versioning

- `record_version` is an integer. This document defines version `1`.
- A writer emits every required key of its version.
- A reader ignores keys it does not know.
- A reader refuses a `record_version` greater than the highest it supports.

## 10. Canonical JSON and digests

### 10.1 Canonical JSON

A canonical JSON text is produced as follows (proposed). Every implementation must produce identical bytes for the same
value, because digests are taken over these bytes.

- UTF-8, no byte-order mark, no insignificant whitespace.
- Object members sorted by key, compared as UTF-8 bytes. Keys are unique.
- Arrays keep their order.
- Strings escape only `"`, `\` and control characters U+0000 to U+001F. The short forms `\b \f \n \r \t` are used
  where they exist; other control characters use `\u` with four lowercase hexadecimal digits. All other characters are
  written as UTF-8.
- Numbers are integers in base 10 with no leading zero, and lie within the range from −2^53 to 2^53. Canonical documents
  contain no fractions and no exponents.
- `true`, `false` and `null` are written in lowercase.

### 10.2 Digest format

A digest is the text `sha256:` followed by 64 lowercase hexadecimal digits: the SHA-256 of the bytes it covers (design:
records store format and digest; proposed: this exact form). Every digest in a record carries the prefix.

### 10.3 Input digest

The input digest of a task covers the content of its bound input values (proposed; the procedure replaces an earlier
one that did not length-prefix file content):

1. Collect every path that appears as a bound value, where a list value contributes each of its paths. Remove
   duplicates and sort the paths as UTF-8 bytes.
2. Start a SHA-256 computation. For each path `P` in that order:
   a. Absorb the bytes of `P`, then one `0x00` byte.
   b. If no regular file lies at or under `P` (relative to the project root), absorb the bytes `missing`, then one
      `0x00` byte, and continue with the next path.
   c. Otherwise, for each regular file `F` at or under `P`, in order of the UTF-8 bytes of its project-relative path:
      absorb that path, one `0x00` byte, the size of `F` as an 8-byte big-endian unsigned integer, and the bytes of `F`.
3. The digest is the result, in the format of section 10.2.

A symbolic link to a regular file counts as that file. A symbolic link to a directory is not descended into.

### 10.4 Other digests

- `plan_digest`: the digest of the canonical JSON of `plan.json` (section 5.5).
- `content_digest`: the digest of the canonical JSON of the recipe document after schema conversion, including its
  `schema` key.
- `inputs_digest`: the digest of the canonical JSON of the `inputs` block after schema conversion.
- `file_digest`: the digest of the bytes of the recipe file.
- `pin_digest`: the `pin.sha256` value of the recipe, in the form of section 10.2.
- The four component digests of section 8.2 use the formats stated there, with the prefix of section 10.2.

## 11. Conformance data layout

The Python prototype and the Rust core must give identical results on the same conformance data (design 1, principle
3). The data lives in `conformance/` at the root of the stradler repository (proposed). A later item writes the data;
this section fixes only its layout.

```
conformance/
  yaml/<case>.yaml              input to the subset reader
  yaml/<case>.expect.json       {"accept": true, "value": <value>} or {"accept": false, "code": "<yaml code>"}
  canonical/cases.json          list of {"value": <JSON>, "canonical": "<text>", "digest": "sha256:..."}
  recipe/<case>/recipe.yaml     recipe under test
  recipe/<case>/structure.toml  areas used by the path checks
  recipe/<case>/expect.json     {"accept": true} or {"accept": false, "errors": [{"code", "path"}]}
  plan/<case>/case.json         {"recipe", "structure", "steps", "named_inputs", "force", "mtimes"}, optionally "omit"
                                (step names a task filter leaves out of the plan, 5.6)
  plan/<case>/fixture/          project files the plan reads (small text files)
  plan/<case>/expect/plan.json  expected plan document
  plan/<case>/expect/digest     expected plan digest
  current/<case>/               as plan, plus records/ with prepared run directories
  input-digest/<case>/          fixture/, values.json (bound values), expect.json ({"digest"})
```

Rules (proposed):

- Case names are lowercase kebab-case. They contain no work-item identifiers and no organization or project names.
- A fixture file is at most 64 KiB; a case is at most 1 MiB.
- `mtimes` in `case.json` sets the modification times of fixture files (ISO UTC), so the output check of section 8.3
  is reproducible on every host.
- Run cases that execute commands name only commands present on every conformance host. Version 1.0.0 covers the plan,
  current and input-digest layers; execution cases are added with the local scheduler.
- Comparison: JSON is parsed; object member order is ignored; array order is significant; digests are compared as exact
  strings; error lists are compared as sets of (code, path) pairs.
- Masked during comparison: `run_id`, `started`, `ended`, `stradler_version`, `stradler_revision` and `mtime_ns`.

## 12. Open items

Each item needs a decision by the Director or a review at the S2-b item.

1. Names renamed from the earlier prototype: `docker` becomes `image`; `template` and `derivative` become `fixed` and
   `named`; `{pipeline_entry}` becomes `{workflow}`; `subjects` and `sessions` become `units`; `input_hash` becomes
   `input_digest`; `pin_sha256` and `resolved_sha256` become `pin_digest` and `resolved_digest`; the version and
   revision fields of the records become `stradler_version` and `stradler_revision`.
2. Left out of version 1.0.0: `when` and `acquisition_default` (domain conditions, to come through data handler
   add-ons); `goals` (domain ids); `derivatives` (export, with the export specification); `select` on `named` inputs (the
   data handler API).
3. An unresolved command placeholder is `not-run` here. The earlier prototype left it literal.
4. A list value joined with `,` cannot bind a path containing a comma. A separate-argument form is the alternative.
5. The name `none` is reserved for the no-container runtime.
6. Glossary additions: unit, unit identifier, level, segment, chain, placeholder; the join entry should drop its
   earlier-prototype wording; the digest form of section 10.2.
7. Canonical JSON (10.1) is not the default output of common JSON writers. Digests taken with those writers differ.
   The earlier prototype used those writers; anything compared with it must be re-digested.
8. Outputs in areas of mutability `sealed` or `immutable` are never removed, and a run that would re-create them is
   `not-run`. The earlier prototype removed any output before a re-run.
9. A task whose outputs exist but which has no reference record is planned, not skipped. This follows the glossary
   definition of current.
10. Reproduction under a new run id (7.3) instead of overwriting a record. The design (4) says "re-run a task"; the
    choice needs a decision.
11. The run status rule and the task state `interrupted` (9.2, 7.1). The earlier prototype's aggregate counted a
    `skipped` task as a failure on one path, and its task-record type refused `interrupted` that another path wrote.
    Version 1.0.0 fixes both; the scheduler add-on must follow.
12. The `revision` field of section 9.2 depends on the version control survey (design 8, decision 7). Until then it
    holds a git commit or null.
13. The input digest of section 10.3 length-prefixes file content. The earlier prototype did not.
14. `inputs.dataset` is a project path, although the glossary places a dataset inside an area. Decide whether the
    recipe should name an area instead.
