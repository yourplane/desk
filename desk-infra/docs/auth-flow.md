# Login and auth flow

## Overview

1. User opens the app (CloudFront URL). CloudFront viewer-request function checks for `desk_token` cookie; if missing, redirects to Cognito hosted UI.
2. User signs in at Cognito; Cognito redirects back to the app with `?code=...`.
3. Frontend runs `handleCallback()`: reads `code` and PKCE `code_verifier` (from sessionStorage or cookie), POSTs to Cognito `/oauth2/token`, receives `id_token`, stores it in sessionStorage and `desk_token` cookie.
4. App shows workstations list and calls `GET /api/workstations` with header `Authorization: Bearer <id_token>`.
5. Request hits CloudFront → API Gateway (HTTP API). Origin Request Policy forwards `Authorization` to the origin. JWT authorizer validates token (issuer = Cognito User Pool URL, audience = app client ID). If valid, request reaches Lambda; if not, 401.

## Important details

- **Callback URL**: Must match exactly in the Cognito app client and in the OAuth request. The CloudFormation template registers the CloudFront URL and, when a custom domain is configured, that HTTPS origin as well. The SPA uses **`window.location.origin`** as `redirect_uri` unless **`VITE_COGNITO_REDIRECT_URI`** is set (override for tests or special layouts).
- **OAuth scopes**: The frontend requests `openid email profile` at `/oauth2/authorize`, matching **`UserPoolAppClient`** in `cloudformation/main.yaml`. The Cognito app client’s **Allowed OAuth scopes** must include those values. If they do not (for example `invalid_scope` / `invalid_request` on redirect back), align the app client or the authorize `scope` string. Refresh tokens still come from the code exchange when the client has refresh token validity and allowed flows configured.
- **Authorize errors**: If Cognito rejects the authorize request, it redirects to the callback URL with `?error=...&error_description=...` (no `code`). The app shows a stable error screen instead of looping `ensureAuth` → `goToLogin`.
- **PKCE verifier**: Stored in sessionStorage and a short-lived cookie when redirecting to login so it survives the redirect back (e.g. same or new tab).
- **API Gateway JWT**: Issuer = `https://cognito-idp.<region>.amazonaws.com/<UserPoolId>`, Audience = app client ID. The `id_token` from Cognito must have matching `iss` and `aud` claims.
