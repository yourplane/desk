# desk-api

HTTP API for desk EC2 workstations. All EC2 logic lives in `desk-sdk`; this layer only parses requests, calls SDK methods, and returns JSON.

## Local development

Run from the **desk-api** directory (the `app` package lives here):

```bash
cd desk-api
uv run uvicorn app.main:app --reload
```

Or from the repo root using the workspace project:

```bash
uv run --project desk-api uvicorn app.main:app --reload
```

Running `uvicorn app.main:app` from the repo root without `--project desk-api` will fail with `ModuleNotFoundError: No module named 'app'`.

- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Workstations: http://localhost:8000/api/workstations

## Endpoints

- `GET /api/workstations` — list workstations
- `POST /api/workstations/{name}/start` — start by name or instance ID
- `POST /api/workstations/{name}/stop` — stop by name or instance ID
- `POST /api/workstations/{name}/kill` — permanently terminate by name or instance ID

### Cloud AMI build (Step Functions)

Requires `DESK_DATA_BUCKET`, `DESK_AMI_BUILD_STATE_MACHINE_ARN`, and optional `DESK_AMI_RECIPES_PREFIX` / `DESK_AMI_BUILDS_PREFIX` (defaults `ami-recipes`, `ami-builds`). Recipe and build records are JSON objects in S3 at `{prefix}/{id}.json`.

- `GET/POST /api/ami-recipes`, `GET/PUT/DELETE /api/ami-recipes/{id}` — recipe CRUD (body matches `desk ami build` JSON; cloud builds require `s3://` copy sources)
- `GET/POST /api/ami-builds`, `GET /api/ami-builds/{id}` — list builds, start execution, optional Step Functions status on get

AWS region/profile come from env (`AWS_REGION`, `AWS_PROFILE`) or desk config.

## Lambda

Use `lambda_handler.handler` as the Lambda handler. Mangum wraps the FastAPI app for API Gateway.
