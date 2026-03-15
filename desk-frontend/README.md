# desk-frontend

React web UI for desk EC2 workstations. Talks to the backend at `/api`.

## Local development

1. Start the API (from repo root or `desk-api`):

   ```bash
   cd desk-api && uv run uvicorn app.main:app --reload
   ```

2. Start the frontend:

   ```bash
   npm run dev
   ```

3. Open http://localhost:5173 — the dev server proxies `/api` to the backend at port 8000.

## Build

```bash
npm run build
```

Output is in `dist/`.
