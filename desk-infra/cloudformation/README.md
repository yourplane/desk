# Desk Web App Stack

CloudFormation (SAM) stack for the desk web app: **basic Cognito** (username/password, no Google), **desk-api Lambda** (built with SAM from `desk-api/`), API Gateway with **built-in JWT authorizer**, S3, CloudFront, WAF.

Auth is handled at the edge: a **CloudFront Function** checks for the `desk_token` cookie and redirects to Cognito hosted UI when missing. API Gateway validates the JWT (Cognito User Pool) with no custom authorizer code.

**Lambda packaging**: The desk-api function is built with **SAM** (`sam build` uses the Makefile in `desk-api/` to copy app, lambda_handler, desk-sdk and install deps). SAM uses its own deployment bucket (managed by the SAM CLI); there is no custom artifacts bucket in this stack.

## Prerequisites

- AWS CLI and **AWS SAM CLI** installed
- Deploy the stack in **us-east-1** (required for WAF attached to CloudFront and for ACM certificates used by CloudFront)
- Node and Python for the deploy script

## Custom domain (optional)

1. In **ACM (us-east-1)**, request or import a certificate for your app hostname. For a follow-on that needs many subdomains under the same zone (e.g. `*.<root-domain>`), include that name or a wildcard in the certificate SANs.
2. Complete **DNS validation** for the certificate (e.g. records at your external DNS provider).
3. Deploy with **`DESK_CUSTOM_DOMAIN_NAME`** and **`DESK_ACM_CERTIFICATE_ARN`** set (see below). The stack adds the name as a CloudFront **alias** and attaches the certificate. Create a **CNAME** (or apex **ALIAS**) at your DNS provider pointing the hostname to the **`CloudFrontDomain`** stack output.
4. Cognito **callback and logout URLs** always include the default **CloudFront** URL and, when a custom domain is configured, the **custom** URL as well. The SPA uses **`window.location.origin`** as the OAuth `redirect_uri` when `VITE_COGNITO_REDIRECT_URI` is unset, so the same build works at either hostname.

## First-time deploy

1. From `desk-infra/scripts`: run `./full-deploy.sh desk-web` (or `./deploy.sh desk-web`). It runs `sam build`, `sam deploy`, builds the frontend, syncs to S3, and invalidates CloudFront.

2. Create a user in the Cognito User Pool (AWS Console → Cognito → User Pools → your pool → Create user), then sign in with the hosted UI.

## One-command deploy

```bash
export AWS_REGION=us-east-1
# Optional custom domain:
# export DESK_CUSTOM_DOMAIN_NAME=desk.example.com
# export DESK_ACM_CERTIFICATE_ARN=arn:aws:acm:us-east-1:123456789012:certificate/...
../scripts/full-deploy.sh desk-web
```

## Subsequent deploys (app only)

`deploy.sh` also updates the **`desk-router`** stack (latest `router-ami-*` in the account) before updating the web app; the VPC stack **`desk`** must already exist.

```bash
../scripts/deploy.sh desk-web
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| `CustomDomainName` | Optional FQDN for the app (e.g. `desk.example.com`). Empty = CloudFront default hostname only. Set via **`DESK_CUSTOM_DOMAIN_NAME`** when using `deploy.sh`. |
| `AcmCertificateArn` | ACM cert ARN in **us-east-1**, required when `CustomDomainName` is set. Set via **`DESK_ACM_CERTIFICATE_ARN`**. |
| `EnableWebRouterCloudFront` | `"true"` only if ACM includes **`*.CustomDomainName`**. Set via **`DESK_ENABLE_WEB_ROUTER_CLOUDFRONT`** (default **`false`**). |
| `Route53HostedZoneId` | Optional **`Z...`** for the **public** hosted zone whose domain name equals **`CustomDomainName`** (e.g. zone `desk.example.com`). When set with web-router CloudFront enabled, the stack creates a **wildcard alias** (`*` record) to the web-router distribution so **`{name}-{port}.desk.example.com`** resolves. Set via **`DESK_ROUTE53_HOSTED_ZONE_ID`**, or set **`DESK_ROUTE53_AUTO_LOOKUP=true`** before **`deploy.sh`** to resolve the zone from **`DESK_CUSTOM_DOMAIN_NAME`**. |

Stack outputs include **`CanonicalAppURL`** (custom HTTPS URL when configured, otherwise CloudFront), **`CloudFrontURL`**, and **`CloudFrontDomain`** (DNS target for the distribution).

## Public web routes (custom domain only)

Set **`DESK_ENABLE_WEB_ROUTER_CLOUDFRONT=true`** when deploying (together with **`DESK_CUSTOM_DOMAIN_NAME`** and **`DESK_ACM_CERTIFICATE_ARN`**) if the ACM certificate includes a **wildcard SAN** for **`*.your-apex`** (e.g. `*.desk.example.com`). If the certificate only covers the apex hostname, leave this **`false`** (default) or the stack update will fail when CloudFront tries to attach the **`*.apex`** alias.

When enabled and DNS is in place:

1. The **`desk-router`** stack creates an **internal ALB** in front of the router ASG (HTTP from CloudFront VPC origins to port **8780** on the instance). Deploy passes the **CloudFront VPC origin** managed prefix list into the ALB security group.
2. This stack adds a **second CloudFront distribution** whose alias is **`*.CustomDomainName`** (e.g. `*.desk.example.com`). It uses a **VPC origin** to that internal ALB. A **CloudFront Function** on viewer-request requires the **`desk_token`** cookie; otherwise it redirects to the SPA origin.
3. **DNS:** Either set **`DESK_ROUTE53_HOSTED_ZONE_ID`** (or **`DESK_ROUTE53_AUTO_LOOKUP=true`**) so **`deploy.sh`** passes **`Route53HostedZoneId`** and the stack creates a **Route 53** wildcard **alias** (`*` in the `desk.example.com` zone) to **`WebRouterCloudFrontDomain`**, **or** manually create a **wildcard** **A/ALIAS** for **`*.desk.example.com`** pointing at that output. Point the **apex** `desk.example.com` at the main app distribution as before.
4. The frontend build sets **`VITE_WEB_ROUTER_HOST_SUFFIX`** and **`VITE_COOKIE_DOMAIN`** when a custom domain is configured so port chips link to **`https://{name}-{port}.your-apex/`** and the auth cookie is visible on those hosts.

### Troubleshooting: “site can’t be reached” or TLS errors on `*.desk.example.com`

- **Cloudflare (or any proxy) in front of the wildcard:** If the wildcard CNAME targets CloudFront but the record is **proxied** (orange cloud), traffic hits Cloudflare’s edge first. Cloudflare’s default **Universal SSL** cert is usually **`*.yourroot.com`**, which **does not** cover **`dev-5173.desk.yourroot.com`** (you need a cert for **`*.desk.yourroot.com`**). The client then often sees **TLS handshake failures** (`ERR_SSL_VERSION_OR_CIPHER_MISMATCH` / similar), while **direct** requests to CloudFront work. **Fix:** set the wildcard record to **DNS only** (grey cloud) so the name resolves to **CloudFront** and the **ACM** cert on the distribution is used, **or** add a **Cloudflare Advanced Certificate** (or custom cert) for **`*.desk.yourroot.com`** and keep proxy on.
- **Verify CloudFront directly:** `dig +short dXXX.cloudfront.net` should show **Amazon** IPs (`3.x`, `13.x`, `18.x`, …). If `dig +short dev-5173.desk.example.com` shows **Cloudflare** IPs (`104.21.x`, `172.67.x`), you are not reaching CloudFront from the public resolver until proxy/DNS is adjusted.
- **New tab jumps back to the desk apex:** The web-router viewer function redirects to **`https://your-apex/`** when **`desk_token`** is missing. Port links open a **new tab**, which does not share **sessionStorage**; only the **cookie** (with **`Domain=.desk.example.com`**) is sent. Reload the desk app once after a deploy that sets **`VITE_COOKIE_DOMAIN`** / **`VITE_WEB_ROUTER_HOST_SUFFIX`** so an existing session re-writes the cookie with the right **Domain** (the SPA does this on load when a session is present).

Without a custom domain, public web route links are not generated; local/router-only behavior is unchanged.

## Local development

Unaffected. Run `npm run dev` and `uvicorn app.main:app --reload`; no auth when `VITE_COGNITO_*` is unset.
