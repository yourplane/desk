# desk-frontend

React web UI for desk EC2 workstations. Talks to the backend at `/api`.

## Local development

1. Start the API (from repo root or from `desk-api`):

   ```bash
   uv run --project desk-api uvicorn app.main:app --reload
   ```
   or: `cd desk-api && uv run uvicorn app.main:app --reload`

2. Start the frontend:

   ```bash
   npm run dev
   ```

   The dev server proxies `/api` to the backend. By default it uses port **8000**. To use a different backend port (e.g. 8888), set `DESK_API_PORT`:

   ```bash
   DESK_API_PORT=8888 npm run dev
   ```

3. Open http://localhost:5173.

## Build

```bash
npm run build
```

Output is in `dist/`.
