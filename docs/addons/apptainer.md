# stradler-apptainer: Apptainer container runtime add-on

Implements the container runtime protocol of `docs/ADDONS.md` section 4. It lives in `src/stradler/ext/apptainer.py`,
imports nothing from the stradler core and uses only the standard library, so it can move to its own package. The
console entry point is `stradler-apptainer`: one JSON request on standard input, one JSON response on standard output.

Configuration (`config.toml`):

```toml
[addons.apptainer]
kind = "container_runtime"
version = "0.1.0"
command = "stradler-apptainer"

[addons.apptainer.settings]
apptainer_command = "apptainer"   # optional; the native command, injectable
```

| Operation | Behaviour |
|---|---|
| `capabilities` | recipe formats `apptainer-def`, `docker`, image formats `sif`, `docker`, platform `linux`, digest `sha256`, binds `ro`/`rw`. |
| `build` | `apptainer build <output> <recipe>`; returns image path, format `sif`, sha256 of the file. |
| `inspect` (docker) | reference only, no network: `format` `docker`, `digest` = the pinned sha256, `size_bytes` null. Unpinned is `refused`. |
| `inspect` | sha256 and size of the file, labels from `apptainer inspect --json`. A directory is `refused`. |
| `build` (docker) | `format` `docker`: `recipe` is `docker://<name>@sha256:<64 hex>`; runs `apptainer build <output> <ref>`; result adds `pin_digest` (reference sha256) and `resolved_digest` (SIF sha256). Unpinned is `refused`. |
| `wrap` | `apptainer exec --containall [--bind src:dst[:ro]]... [--pwd dir] image argv...`. |

`--containall` disables the host mounts Apptainer adds by default (ADDONS P9); every bind comes from `binds`. A bind
source must be absolute with no `..` segment, and its mode must be `ro` or `rw`. Errors use the closed code set of
ADDONS 1.4 and the exit codes of 1.5 (2 invalid request, 3 unsupported version, 1 other failures, 4 internal).

Tests (`tests/ext/test_apptainer.py`) use a fake executable and never run Apptainer.
