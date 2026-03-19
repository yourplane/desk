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

AWS region/profile come from env (`AWS_REGION`, `AWS_PROFILE`) or desk config.

## Lambda

Use `lambda_handler.handler` as the Lambda handler. Mangum wraps the FastAPI app for API Gateway.
