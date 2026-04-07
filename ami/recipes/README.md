# AMI recipes

## `default-desk-ami.json`

Defines a **`build`** phase (provision the image) and a **`test`** phase (run on a fresh instance launched from the registered AMI).

Tests are split into two steps:
- `desk list` (validates `desk` is installed; the build symlinks it to `/usr/local/bin` so SSM’s non-login shell can find it).
- `test-home-dir-empty.sh` (validates `/home/ubuntu` has no unexpected top-level entries).

### Run a full build + test in AWS

From the **repository root** (paths in the recipe are relative to `ami/recipes/`):

```bash
uv sync
uv run desk ami build run ami/recipes/default-desk-ami.json
```

Requires a deployed desk stack (S3 copy bucket, VPC, etc.), AWS credentials, and the `desk` CLI configured for that account/region.
