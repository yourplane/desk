# Lambda test events

Sample payloads for invoking the desk-api Lambda directly (API Gateway HTTP API payload 2.0).

**Example – GET /api/workstations:**

```bash
aws lambda invoke --function-name desk-web-api \
  --payload fileb://events/get-instances.json \
  --cli-binary-format raw-in-base64-out \
  out.json && cat out.json
```

Expect `statusCode: 200` and a JSON body array of workstations (or `[]`).
