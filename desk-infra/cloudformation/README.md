# Desk Web App Stack

CloudFormation stack for the desk web app: **basic Cognito** (username/password, no Google), Lambda (desk-api), API Gateway with **built-in JWT authorizer**, S3, CloudFront with **viewer-request auth** (redirect to Cognito if no cookie), WAF.

Auth is handled at the edge: a **CloudFront Function** checks for the `desk_token` cookie and redirects to Cognito hosted UI when missing. API Gateway validates the JWT (Cognito User Pool) with no custom authorizer code.

## Prerequisites

- AWS CLI configured
- Deploy the stack in **us-east-1** (required for WAF attached to CloudFront)
- Node and Python for the deploy script

## First-time deploy

1. Create the stack (single parameter: callback URL; use placeholder first):

   ```bash
   aws cloudformation deploy \
     --template-file main.yaml \
     --stack-name desk-web \
     --parameter-overrides CognitoCallbackURL=https://placeholder.example.com \
     --capabilities CAPABILITY_IAM
   ```

2. Set the real Cognito callback URL from the CloudFront output and redeploy:

   ```bash
   CF_URL=$(aws cloudformation describe-stacks --stack-name desk-web --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text)
   aws cloudformation deploy \
     --template-file main.yaml \
     --stack-name desk-web \
     --parameter-overrides "CognitoCallbackURL=$CF_URL" \
     --capabilities CAPABILITY_IAM
   ```

3. Run the deploy script to build frontend, upload desk-api Lambda, sync S3, and invalidate CloudFront:

   ```bash
   ../scripts/deploy.sh desk-web
   ```

4. Create a user in the Cognito User Pool (AWS Console → Cognito → User Pools → your pool → Create user), then sign in with the hosted UI.

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
