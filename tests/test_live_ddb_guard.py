"""The live-DynamoDB safety guard must refuse everything except a throwaway table, non-root caller, approved region and explicit opt-in. Offline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_ddb_guard import LiveTargetRefused, assert_safe_live_target  # noqa: E402

OK = dict(identity_arn="arn:aws:sts::123456789012:assumed-role/EnigmaDeployer/session", table_name="enigma-parity-a1b2c3d4", table_status="ACTIVE", item_count=0,
          tags={"Purpose": "enigma-parity-throwaway"}, region="us-east-1", approved_region="us-east-1", env={"ENIGMA_ALLOW_NETWORK": "1", "ENIGMA_LIVE_DDB_APPROVED": "1"})


def go(**over):
    assert_safe_live_target(**{**OK, **over})


def test_a_proper_throwaway_target_is_accepted():
    go()
    go(identity_arn="arn:aws:iam::123456789012:user/deployer")


@pytest.mark.parametrize("over,msg", [
    ({"identity_arn": "arn:aws:iam::123456789012:root"}, "ROOT"),
    ({"identity_arn": "arn:aws:sts::123456789012:root"}, "ROOT"),
    ({"identity_arn": ""}, "ROOT"),
    ({"identity_arn": "not-an-arn"}, "unrecognised"),
    ({"table_name": "enigma-dev"}, "table name"),
    ({"table_name": "enigma-prod"}, "table name"),
    ({"table_name": "enigma-parity-"}, "table name"),
    ({"table_name": "enigma-parity-UPPER"}, "table name"),
    ({"table_name": "enigma-parity-ab"}, "table name"),
    ({"table_name": "other-enigma-parity-a1b2c3d4"}, "table name"),
    ({"tags": {}}, "not tagged"),
    ({"tags": {"Purpose": "production"}}, "not tagged"),
    ({"table_status": "CREATING"}, "status"),
    ({"item_count": 100_001}, "must be small"),
    ({"region": "eu-west-1"}, "not the owner-approved"),
    ({"approved_region": None}, "not the owner-approved"),
    ({"env": {"ENIGMA_LIVE_DDB_APPROVED": "1"}}, "ENIGMA_ALLOW_NETWORK"),
    ({"env": {"ENIGMA_ALLOW_NETWORK": "1"}}, "ENIGMA_LIVE_DDB_APPROVED"),
    ({"env": {}}, "ENIGMA_ALLOW_NETWORK"),
])
def test_every_unsafe_target_is_refused(over, msg):
    with pytest.raises(LiveTargetRefused, match=msg):
        go(**over)
