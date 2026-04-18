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

Stack outputs include **`CanonicalAppURL`** (custom HTTPS URL when configured, otherwise CloudFront), **`CloudFrontURL`**, and **`CloudFrontDomain`** (DNS target for the distribution).

## Local development

Unaffected. Run `npm run dev` and `uvicorn app.main:app --reload`; no auth when `VITE_COGNITO_*` is unset.
