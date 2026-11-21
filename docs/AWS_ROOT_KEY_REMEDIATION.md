# AWS root access-key remediation and least-privilege deployer plan

**Status: PLAN ONLY. Nothing here has been executed.** No IAM user, role, key, permission set or policy was created, rotated or deleted, and no key was removed. Every step below is for **you** to perform or to authorise explicitly.

## 1. Finding (read-only evidence, 2026-11-21)
- The configured CLI profile `default` authenticates as the account **root user** (`arn:aws:iam::<acct>:root`).
- `iam get-account-summary` reports **root access keys present** and **MFA enabled** on the root user.
- The second profile, `vllm-temp`, has **no credentials and no region** configured, so there is **no non-root path** today.
- Consequence (skills.md §16.3): no deployment, automation or chargeable operation may use root. Per policy, read-only AWS calls that need root were **stopped** this session after the identity check; the facts in `AWS_COST_MATRIX.md` come from earlier read-only calls.

## 2. Why root keys are a problem
Root has unlimited power (including closing the account and changing billing). A leaked root access key cannot be restricted by IAM policy. Long-lived keys on a laptop disk are the most common way accounts are lost. AWS guidance is to have **no** root access keys and to use IAM Identity Center (or IAM roles) with short-lived credentials.

## 3. Target state
| Item | Target |
|---|---|
| Daily/deploy identity | IAM Identity Center user with a **permission set** (short-lived SSO credentials), MFA/passkey required |
| Root user | MFA (already on), **no access keys**, used only for the few root-only tasks |
| CLI profile | `enigma-deployer` via `aws configure sso`, never access keys in `~/.aws/credentials` |
| GPU / AI services | **explicitly denied** by the deployer's policy (see §5), not merely avoided |
| Cost | a budget **notification** (not a cap) after you approve it |

## 4. Owner-supervised checklist (do in this order; stop at any surprise)
1. **Console, root user** (once): Billing → *IAM user and role access to Billing information* → enable (so the deployer can see costs read-only).
2. **Enable IAM Identity Center** in the region you approve (`us-east-1` suggested). The account instance has no charge. Create **one** user (you), enrol MFA/passkey.
3. **Create a permission set** `EnigmaBootstrapAdmin` (AWS-managed `AdministratorAccess`, **session 1 h**) *only to bootstrap*, assign it to your user for this account. Use it to create the narrower set in step 5, then **remove it**.
4. `aws configure sso --profile enigma-deployer` (start URL and region from step 2). Verify:
   `aws sts get-caller-identity --profile enigma-deployer` must show an **assumed-role**, **not** `:root`.
5. **Create the real permission set** `EnigmaDeployer` using the draft policy in §5 (review it line by line; it is unreviewed), assign it, delete the bootstrap assignment.
6. **Prove it works before touching root keys:** with `enigma-deployer`, run only read-only calls (`sts get-caller-identity`, `lambda list-functions`, `dynamodb list-tables`, `cloudformation validate-template` on the drafts in `infra/aws/`) and confirm that a **denied** call is denied (e.g. `aws ec2 run-instances --dry-run` must return an authorisation error).
7. **Only now**, and only with your explicit say-so: Console → *Security credentials* → root *Access keys* → **Make inactive** (reversible). Wait several days; if nothing breaks, **Delete** them. Replace the `default` profile's root key in `~/.aws/credentials` by removing that profile (do it yourself; never paste keys into chat, Git or docs).
8. Record the date and the new profile name privately (not in Git).

## 5. Draft deployer policy (UNREVIEWED; adapt names, then review)
Intent: allow only the control-plane services Enigma needs, scoped by name prefix, and **deny** everything the project forbids.
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "ControlPlaneServices", "Effect": "Allow",
     "Action": ["lambda:*", "dynamodb:*", "cognito-idp:*", "logs:*", "cloudfront:*", "s3:*", "cloudformation:*", "iam:PassRole", "iam:CreateRole", "iam:DeleteRole",
                "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetRole", "iam:GetPolicy",
                "iam:TagRole", "iam:TagPolicy", "ssm:GetParameter", "ssm:PutParameter", "budgets:ViewBudget", "ce:Get*", "pricing:*"],
     "Resource": "*"},
    {"Sid": "NoGpuNoAcceleratorNoHostedAi", "Effect": "Deny",
     "Action": ["sagemaker:*", "bedrock:*", "bedrock-runtime:*", "elasticinference:*", "ec2:RunInstances", "ec2:RequestSpotInstances", "ec2:RequestSpotFleet",
                "ec2:CreateFleet", "ec2:CreateLaunchTemplate", "ec2:PurchaseHostReservation", "ec2:AllocateHosts", "batch:*", "ecs:*", "eks:*", "autoscaling:*"],
     "Resource": "*"},
    {"Sid": "NoBillableNetworkingByAccident", "Effect": "Deny",
     "Action": ["ec2:CreateNatGateway", "ec2:AllocateAddress", "elasticloadbalancing:*", "route53:CreateHostedZone", "route53domains:*", "secretsmanager:CreateSecret",
                "wafv2:Create*", "rds:Create*"],
     "Resource": "*"},
    {"Sid": "NeverTouchBillingOrIdentityRoot", "Effect": "Deny",
     "Action": ["account:*", "aws-portal:Modify*", "billing:Update*", "iam:CreateAccessKey", "iam:CreateUser", "iam:DeleteAccessKey", "iam:UpdateAccessKey", "organizations:*", "sso:*"],
     "Resource": "*"}
  ]
}
```
Notes: `rds:Create*` and `ec2:RunInstances` stay **denied** because Track A (RDS) needs a separate, explicit decision and Track B needs neither. If you later approve Track A, relax only `rds:Create*` and add the VPC actions deliberately. `s3:*`, `cloudfront:*` and `lambda:*` should be narrowed to `enigma-*` resource ARNs once the exact inventory is approved. A deny in this policy is a **technical** guarantee that even a coding mistake cannot launch a GPU.

## 6. What this plan does not do
- It does not create the IAM Identity Center instance, users, permission sets or profile; it does not delete or deactivate any key; it does not enable any budget; it does not verify the credit expiry date (Billing → Credits, console only).
- It does not make deployment free. See `AWS_COST_MATRIX.md` and `AWS_DEPLOYMENT.md` for the itemised charges and the approval gate.
