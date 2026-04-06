# AMI recipes

## `default-desk-ami.json`

Defines a **`build`** phase (provision the image) and a **`test`** phase (run on a fresh instance launched from the registered AMI).

Tests verify that `desk list` works and that `/home/ubuntu` has no unexpected top-level entries (only dotfiles/dotdirs and the `desk/` checkout). Implementation: `test-default-desk-ami.sh`.

### Run a full build + test in AWS

From the **repository root** (paths in the recipe are relative to `ami/recipes/`):

```bash
uv sync
uv run desk ami build run ami/recipes/default-desk-ami.json
```

Requires a deployed desk stack (S3 copy bucket, VPC, etc.), AWS credentials, and the `desk` CLI configured for that account/region.

Or use `./ami/recipes/run-default-ami-build.sh` (same as above).

### CI (GitHub Actions)

Copy `default-desk-ami-github-actions.yml` to `.github/workflows/default-desk-ami.yml` in this repo. Trigger **workflow_dispatch** manually; set secrets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION`.
