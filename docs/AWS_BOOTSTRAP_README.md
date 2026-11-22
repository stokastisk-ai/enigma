# Enigma AWS assisted setup — Python 3.13

This package is grounded in the current uploaded Enigma AWS documents. It does **not** create IAM Identity Center users, assign permission sets, read billing credit expiry, or deactivate root keys automatically; those steps require the account owner in the AWS console. The script can configure a local SSO CLI profile and verify the non-root assumed-role session. Its **only** AWS resource-writing mode is an explicitly confirmed, single-table CloudFormation DynamoDB parity run, with automatic deletion attempted even if tests fail.

1. Copy `enigma_aws_setup.py` to `Project-Enigma/scripts/` and `CLAUDE_CLI_AWS_BOOTSTRAP_PROMPT.md` to the project root.
2. In your Ubuntu project, run `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py plan`.
3. In the AWS console, set up IAM Identity Center, one user with MFA, a carefully scoped deployment permission set, and account assignment. Do not use the broad draft IAM policy without review.
4. Run `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py sso`. It launches `aws configure sso --profile enigma-deployer`, followed by `aws sso login`; it verifies the resulting identity.
5. Open Billing → Credits and enter the actual credit-expiration date using `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py approve`. Region and parity-only spending ceiling are saved to `~/.config/enigma/aws-approval.json` (mode 0600). This does NOT approve a production deployment or the actual live parity write.
6. Run `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py preflight` and `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py parity --suffix testabc123` (dry run).
7. Review the CloudFormation template, live test guard, approval and rollback. Only if satisfied, run `/usr/local/bin/python3.13 scripts/enigma_aws_setup.py parity --suffix testabc123 --execute`, and type its exact explicit confirmation. This may incur AWS charges; a credit balance is not a spending limit. The script waits for stack deletion but cannot guarantee deletion if AWS fails. Verify in the AWS console and Billing.
8. Sign into AWS console yourself, deactivate the root keys after a working non-root SSO session is proven, and later delete them as planned. Never paste AWS root keys or MFA into a coding agent.

This is an operational helper, not a server or a deployment of the Enigma website. It uses no Python third-party dependencies and does not touch the local NVIDIA GPU.
