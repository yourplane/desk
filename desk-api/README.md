# desk-api

HTTP API for desk EC2 workstations. All EC2 logic lives in `desk-sdk`; this layer only parses requests, calls SDK methods, and returns JSON.

## Local development

From the repo root (or `desk-api` with `desk-sdk` on the path):

```bash
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Instances: http://localhost:8000/api/instances

## Endpoints

- `GET /api/instances` — list workstations
- `POST /api/instances/{name}/start` — start by name or instance ID
- `POST /api/instances/{name}/stop` — stop by name or instance ID

AWS region/profile come from env (`AWS_REGION`, `AWS_PROFILE`) or desk config.

## Lambda

Use `lambda_handler.handler` as the Lambda handler. Mangum wraps the FastAPI app for API Gateway.
