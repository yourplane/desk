# Desk Web App Stack

CloudFormation stack for the desk web app: Cognito (Google login), Lambda (desk-api), API Gateway, S3, CloudFront, WAF.

## Prerequisites

- AWS CLI configured
- Deploy the stack in **us-east-1** (required for WAF attached to CloudFront)
- Google OAuth 2.0 client (Cloud Console): create credentials, add authorized redirect URI `https://<cognito-domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse` for your Cognito domain
- Node and Python for the deploy script

## First-time deploy

1. Create the stack with required parameters:

   ```bash
   aws cloudformation deploy \
     --template-file main.yaml \
     --stack-name desk-web \
     --parameter-overrides \
       AllowedEmail=you@example.com \
       GoogleClientId=YOUR_GOOGLE_CLIENT_ID \
       GoogleClientSecret=YOUR_GOOGLE_CLIENT_SECRET \
     --capabilities CAPABILITY_IAM
   ```

2. Get the CloudFront URL from outputs, then update the stack with the real Cognito callback URL:

   ```bash
   CF_URL=$(aws cloudformation describe-stacks --stack-name desk-web --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text)
   aws cloudformation deploy \
     --template-file main.yaml \
     --stack-name desk-web \
     --parameter-overrides \
       AllowedEmail=you@example.com \
       GoogleClientId=YOUR_GOOGLE_CLIENT_ID \
       GoogleClientSecret=YOUR_GOOGLE_CLIENT_SECRET \
       CognitoCallbackURL="$CF_URL" \
     --capabilities CAPABILITY_IAM
   ```

3. In Google Cloud Console, add the Cognito redirect URI to your OAuth client (e.g. `https://<domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse` — or use the callback URL shown in Cognito).

4. Run the deploy script to build frontend, upload Lambda code, sync S3, and invalidate CloudFront:

   ```bash
   ../scripts/deploy.sh desk-web
   ```

## Subsequent deploys

```bash
../scripts/deploy.sh desk-web
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| AllowedEmail | Only this email can use the app |
| GoogleClientId | Google OAuth client ID |
| GoogleClientSecret | Google OAuth client secret |
| CognitoCallbackURL | Callback URL (set to CloudFront URL after first deploy) |

## Local development

Unaffected. Run `npm run dev` and `uvicorn app.main:app --reload` as before; no auth when VITE_COGNITO_* is unset.
