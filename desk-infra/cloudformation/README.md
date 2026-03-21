# Desk Web App Stack

CloudFormation (SAM) stack for the desk web app: **basic Cognito** (username/password, no Google), **desk-api Lambda** (built with SAM from `desk-api/`), API Gateway with **built-in JWT authorizer** plus an **AWS_IAM-protected reap route**, S3, CloudFront with **viewer-request auth** (redirect to Cognito if no cookie), WAF.

Auth is handled at the edge: a **CloudFront Function** checks for the `desk_token` cookie and redirects to Cognito hosted UI when missing. API Gateway validates the JWT (Cognito User Pool) with no custom authorizer code.

**Lambda packaging**: The desk-api function is built with **SAM** (`sam build` uses the Makefile in `desk-api/` to copy app, lambda_handler, desk-sdk and install deps). SAM uses its own deployment bucket (managed by the SAM CLI); there is no custom artifacts bucket in this stack.

## Prerequisites

- AWS CLI and **AWS SAM CLI** installed
- Deploy the stack in **us-east-1** (required for WAF attached to CloudFront)
- Node and Python for the deploy script

## First-time deploy

Use the one-command deploy (see below), or:

1. From `desk-infra/scripts`: run `./full-deploy.sh desk-web`. It will SAM build, deploy with placeholder callback, then redeploy with the real CloudFront URL, then build frontend and sync.

2. Create a user in the Cognito User Pool (AWS Console → Cognito → User Pools → your pool → Create user), then sign in with the hosted UI.

## One-command deploy

```bash
export AWS_REGION=us-east-1
../scripts/full-deploy.sh desk-web
```

## Subsequent deploys (app only)

```bash
../scripts/deploy.sh desk-web
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| CognitoCallbackURL | Callback URL for Cognito hosted UI (set to CloudFront URL after first deploy) |

## Local development

Unaffected. Run `npm run dev` and `uvicorn app.main:app --reload`; no auth when VITE_COGNITO_* is unset.
