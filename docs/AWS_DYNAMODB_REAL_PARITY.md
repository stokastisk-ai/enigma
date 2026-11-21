# Real-DynamoDB parity run — checklist, guard and cost (NOT RUN)

**Status: NOT RUN. No table exists.** Track B (`store/dynamodb.py`) has passed its behavioural suite only against **moto**, an in-process emulator. This document is the plan for the single real-table run that would let it be called equivalent to the PostgreSQL store. It needs **your approval** (see §6). Nothing here creates, changes or deletes anything in AWS.

## 1. What the real run adds over moto
moto does not model: real throttling (`ProvisionedThroughputExceededException`) and burst capacity, partition limits, genuine transaction-conflict timing between concurrent callers, eventual TTL deletion (up to ~48 h lag), item/response size limits (400 KB items, 1 MB query pages), consistency behaviour, or per-call latency. The real run exists to exercise exactly those.

## 2. The suite that runs
The **same 78 tests** as the emulator run (`tests/api/test_dynamodb_store.py`): the whole existing API suite re-run on `DynamoStore`, 8-thread claim race, concurrent ordered events, submit caps under a race, idempotent submit, failure injection, pagination (forced small pages), `UnprocessedKeys/Items` retries, transaction conflicts, retention, durability, isolation. Two tests change meaning on a real table and must be read, not just counted: the **race tests** (real contention may expose conditional-write ordering moto hides) and **TTL** (the suite asserts correctness never *depends* on TTL; it must not assert immediate deletion).

## 3. Safety guard (built, unit-tested offline)
`tests/live_ddb_guard.py` + `tests/test_live_ddb_guard.py` (20 tests). The live path refuses to delete or write anything unless **all** hold: `ENIGMA_ALLOW_NETWORK=1` (set before pytest starts) · `ENIGMA_LIVE_DDB_APPROVED=1` · caller is **not root** and is a recognisable assumed-role/user ARN · region equals `ENIGMA_LIVE_DDB_APPROVED_REGION` · table name matches `enigma-parity-<6-32 lowercase alphanumerics>` · table tagged `Purpose=enigma-parity-throwaway` · status `ACTIVE` · ≤ 100,000 items. Every refusal is tested. The default test run never reads these variables.

## 4. Throwaway table (draft, not deployed)
`infra/aws/dynamodb-parity.yaml`: `enigma-parity-<suffix>`, **PROVISIONED 25 RCU / 25 WCU, STANDARD class**, no GSI, no PITR, TTL on `ttl`, tagged as throwaway. 25/25 is the documented monthly free-tier maximum, but **whether that allowance applies to this PAID-plan account is unverified**. If the run throttles at 25 WCU that is a *finding to record* (the store retries; it must still end consistent), not a reason to quietly raise capacity.

## 5. Cost estimate for the run (measured, not guessed)
The emulator run of the 78 tests made, per the call counter: GetItem 1,697 · TransactWriteItems 646 (2,595 items) · Query 594 · UpdateItem 234 · BatchGetItem 210 (601 keys) · PutItem 112 · BatchWriteItem 48 (182 items) · Scan 6 · DeleteItem 3 (+ the per-test table clean-up scan/deletes on a real table).
Rough upper bound: **≈ 5,700 write units** (transactional writes counted double) and **≈ 2,900 read units** per full run. At on-demand list prices ($0.625 per million write units, $0.125 per million read units, us-east-1 Price List API) that is **≈ $0.004**; on provisioned 25/25 it consumes capacity already within the free allowance but may throttle. Add the per-test clean-up (a few hundred more units) and a few runs for debugging: **well under $0.10 even at list price**. Table storage is a few MB for minutes. The dominant risk is **not** the bill, it is **forgetting to delete the table**: a provisioned 25/25 table left running costs about $0.0 within the free tier but about **$14 a month** at list price (25×$0.00065×730 + 25×$0.00013×730), so teardown (§7) is part of the approval.

## 6. What I need from you before this can run
1. A **non-root** identity (docs/AWS_ROOT_KEY_REMEDIATION.md), verified read-only first.
2. An **approved region** and an explicit dollar ceiling for this run (suggest $1).
3. Permission to create **exactly one** CloudFormation stack from `infra/aws/dynamodb-parity.yaml`, nothing else.
4. Confirmation of the credit expiry date and whether always-free allowances apply.
5. Confirmation that I may **delete the stack myself** when the run ends (otherwise you must).

## 7. Procedure (after approval; stop on any surprise)
```bash
# 1. non-root, read-only sanity first
aws sts get-caller-identity --profile enigma-deployer --query Arn --output text      # must NOT end in :root
# 2. create the throwaway stack (the ONE approved write)
aws cloudformation deploy --profile enigma-deployer --region <approved> --stack-name enigma-parity-<suffix> \
    --template-file infra/aws/dynamodb-parity.yaml --parameter-overrides Suffix=<suffix>
# 3. run the parity suite against it (never in the default test run)
ENIGMA_ALLOW_NETWORK=1 ENIGMA_LIVE_DDB=1 ENIGMA_LIVE_DDB_APPROVED=1 \
ENIGMA_LIVE_DDB_PROFILE=enigma-deployer ENIGMA_LIVE_DDB_REGION=<approved> ENIGMA_LIVE_DDB_APPROVED_REGION=<approved> \
ENIGMA_LIVE_DDB_TABLE=enigma-parity-<suffix> /usr/local/bin/python3.13 -m pytest tests/api/test_dynamodb_store.py -p no:cacheprovider
# 4. TEARDOWN immediately, then verify it is gone
aws cloudformation delete-stack --profile enigma-deployer --region <approved> --stack-name enigma-parity-<suffix>
aws dynamodb list-tables --profile enigma-deployer --region <approved>               # the table must no longer be listed
```
Record: pass/fail per test, any throttling seen and how the store recovered, consumed capacity (CloudWatch `ConsumedRead/WriteCapacityUnits` for the run window), TTL lag if observed, and the teardown confirmation. Only then does this document's status change.

## 8. What passing would and would not prove
Passing proves the store behaves correctly on real DynamoDB for this suite at this load. It does **not** prove production scale, multi-region behaviour, behaviour behind real Lambda cold starts, or that costs match the model. A failure on a real table is expected to be informative, not embarrassing: record it, fix the store, re-run on a fresh throwaway table.
