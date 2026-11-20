# stradler-slurm: Slurm scheduler add-on

Implements the scheduler protocol of `ADDONS.md` section 2 for Slurm. It is a separate program
(`stradler-slurm`, module `stradler.ext.slurm`), standard library only, and does not import the core planner or
executor: the JSON protocol over standard input and output is its whole interface. It can move to its own package
later.

## Install

Installing the `stradler` package installs the console entry point `stradler-slurm`
(`[project.scripts] stradler-slurm = "stradler.ext.slurm:main"`).

## config.toml entry

```toml
[addons.slurm]
kind = "scheduler"
version = "0.1.0"
command = "stradler-slurm"

[addons.slurm.settings]
partition = "example"   # optional: --partition
account = "example"     # optional: --account
qos = "example"         # optional: --qos
```

The native commands are looked up on `PATH`. To use other executables, set the environment variables
`STRADLER_SLURM_SBATCH`, `STRADLER_SLURM_SQUEUE`, `STRADLER_SLURM_SACCT`, `STRADLER_SLURM_SCANCEL`. Tests set them to
fake executables; they never call real Slurm.

## Operations

| Operation | Native commands | Notes |
|---|---|---|
| `capabilities` | none | non-blocking, dependency kinds `success` and `end`, arrays, resources `cpus`, `memory`, `time`, `gpus`, job name at most 128 characters. |
| `submit` | `sbatch --parsable` | One call per job; the job script goes to standard input and runs `argv` with `exec` (every argument quoted). Sets `STRADLER_JOB_ID` and, for an array, `STRADLER_TASK_INDEX`. |
| `status` | `squeue -r`, then `sacct -X` | Jobs missing from both are `lost`. Array ids expand to one row per task. |
| `cancel` | `scancel`, then `status` | An already ended job (`Invalid job id`) is not an error. |

### submit mapping

| Request | sbatch option |
|---|---|
| `name` | `--job-name` |
| `cwd` | `--chdir` |
| `log_dir`, `name` | `--output=<log_dir>/<name>[-%a].out`, `--error=...err` |
| `array` N | `--array=0-<N-1>` |
| dependency `on: "end"` | `--dependency=afterany:<ids>` |
| dependency `on: "success"` | `--dependency=afterok:<ids>` |
| `resources.cpus` / `memory` / `time` | `--cpus-per-task` / `--mem` / `--time` |
| `resources.gpus` (above 0) | `--gres=gpu:<n>` |
| `settings` partition, account, qos | `--partition`, `--account`, `--qos` |
| `submission_key`, `name` | `--comment=stradler:<key>:<name>` |

`afterany` is satisfied by every ending outcome, including a job that already ended, which is what the finalize job
(ADDONS section 3) needs.

Retries (ADDONS 2.4): before submitting, the add-on looks for jobs whose comment starts with `stradler:<key>:` in
`squeue` and `sacct`. If every job of the request is found, it returns their ids and submits nothing.

`submit` is all or nothing: if an `sbatch` call fails, the jobs already submitted in the call are cancelled and the
response is `backend_error` with the exit code and standard error in `details`.

### State mapping

| Slurm state | Protocol state |
|---|---|
| PENDING, CONFIGURING, REQUEUED, REQUEUE_HOLD, REQUEUE_FED, RESV_DEL_HOLD, SPECIAL_EXIT | `pending` |
| RUNNING, COMPLETING, SIGNALING, STAGE_OUT, RESIZING, SUSPENDED, STOPPED, UPDATE_DB | `running` |
| COMPLETED | `completed` |
| FAILED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL, BOOT_FAIL, DEADLINE, PREEMPTED | `failed` |
| CANCELLED (with or without `by <uid>` or `+`), REVOKED; PENDING with reason `DependencyNeverSatisfied` | `cancelled` |
| not in `squeue` or `sacct` | `lost` |
| any other state | `backend_error` (never guessed) |

`exit_code` is the first field of Slurm's `ExitCode` for `completed` and `failed`; when it is 0 but a signal is set,
`128 + signal`; a `failed` job never reports 0 (it reports 1).

## Exit codes

0 success; 1 error envelope; 2 invalid request; 3 unsupported protocol version; 4 internal failure (ADDONS 1.5).
