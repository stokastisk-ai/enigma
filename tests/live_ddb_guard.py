"""Safety guard for the OPTIONAL real-DynamoDB parity run (tests/api/test_dynamodb_store.py with ENIGMA_LIVE_DDB=1).

The live run deletes every item in the target table before each test, so the target must be provably a throwaway parity table, the caller must be a
NON-ROOT identity, and the region must be the one the owner approved. Every refusal below is unit-tested offline (tests/test_live_ddb_guard.py),
so the live path cannot be pointed at real data by a typo. Nothing here calls AWS: callers pass in what they already read.
"""
from __future__ import annotations

import re

TABLE_PREFIX = "enigma-parity-"
REQUIRED_TAG = ("Purpose", "enigma-parity-throwaway")
MAX_ITEMS = 100_000                                  # a throwaway table must be small; refuse anything that looks like real data


class LiveTargetRefused(RuntimeError):
    pass


def assert_safe_live_target(*, identity_arn: str, table_name: str, table_status: str, item_count: int | None, tags: dict[str, str], region: str, approved_region: str | None,
                            env: dict[str, str]) -> None:
    if env.get("ENIGMA_ALLOW_NETWORK") != "1":
        raise LiveTargetRefused("ENIGMA_ALLOW_NETWORK=1 must be set before pytest starts (the test network guard is on by default)")
    if env.get("ENIGMA_LIVE_DDB_APPROVED") != "1":
        raise LiveTargetRefused("ENIGMA_LIVE_DDB_APPROVED=1 is required: the owner must have approved the throwaway table, region and cost ceiling")
    if not identity_arn or identity_arn.endswith(":root") or ":root" in identity_arn.split("/")[0]:
        raise LiveTargetRefused("refusing: the caller is the ROOT user. Use the non-root deployer identity.")
    if not re.fullmatch(r"arn:aws[a-z-]*:(sts|iam)::\d{12}:(assumed-role|user|role)/.+", identity_arn):
        raise LiveTargetRefused("refusing: unrecognised caller identity")
    if not approved_region or region != approved_region:
        raise LiveTargetRefused(f"refusing: region {region!r} is not the owner-approved region {approved_region!r}")
    if not re.fullmatch(re.escape(TABLE_PREFIX) + r"[a-z0-9]{6,32}", table_name):
        raise LiveTargetRefused(f"refusing: table name must match {TABLE_PREFIX}<6-32 lowercase alphanumerics>")
    if tags.get(REQUIRED_TAG[0]) != REQUIRED_TAG[1]:
        raise LiveTargetRefused(f"refusing: table is not tagged {REQUIRED_TAG[0]}={REQUIRED_TAG[1]}")
    if table_status != "ACTIVE":
        raise LiveTargetRefused(f"refusing: table status is {table_status}")
    if item_count is not None and item_count > MAX_ITEMS:
        raise LiveTargetRefused(f"refusing: table already holds {item_count} items; a parity table must be small")
