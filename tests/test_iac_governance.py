"""Governance of the unapplied IaC/policy artifacts: nothing is approved, nothing GPU, least privilege, and the policies match what the code really calls."""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from enigma_api.awsops import Manifest  # noqa: E402

DEPLOYER = json.loads((ROOT / "infra/aws/iam/enigma-deployer-policy.json").read_text())
READER = json.loads((ROOT / "infra/aws/iam/enigma-api-ops-reader-policy.json").read_text())
MANIFEST = yaml.safe_load((ROOT / "infra/aws/resource-manifest.yaml").read_text())

# IAM action for every boto3 call awsops.py makes (tests/api/test_awsops.py proves nothing else is ever called)
READER_ACTIONS = {"lambda:GetFunctionConfiguration", "dynamodb:DescribeTable", "dynamodb:DescribeTimeToLive", "cloudfront:GetDistribution", "cognito-idp:DescribeUserPool", "cloudwatch:GetMetricData"}
GPU_AND_AI = ["sagemaker:*", "bedrock:*", "ec2:RunInstances", "eks:*", "ecs:*", "batch:*"]


def statements(policy, effect):
    return [s for s in policy["Statement"] if s["Effect"] == effect]


def denied(policy) -> set[str]:
    return {a for s in statements(policy, "Deny") for a in s["Action"]}


def allowed(policy) -> set[str]:
    return {a for s in statements(policy, "Allow") for a in s["Action"]}


def test_policies_are_valid_json_iam_shaped():
    for p in (DEPLOYER, READER):
        assert p["Version"] == "2012-10-17" and p["Statement"]
        for s in p["Statement"]:
            assert s["Effect"] in ("Allow", "Deny") and s["Action"] and s["Resource"] and "Sid" in s
            assert set(s) <= {"Sid", "Effect", "Action", "Resource", "Condition"}, set(s)        # no free-text keys that real IAM would reject
    assert len({s["Sid"] for s in DEPLOYER["Statement"]}) == len(DEPLOYER["Statement"])


def test_deployer_explicitly_denies_every_gpu_accelerator_and_hosted_ai_path():
    d = denied(DEPLOYER)
    for a in GPU_AND_AI + ["elasticinference:*", "ec2:RequestSpotInstances", "ec2:CreateFleet", "bedrock-runtime:*", "autoscaling:*"]:
        assert a in d, a
    # and nothing the deployer is ALLOWED matches a denied GPU/AI action (a deny would be dead code otherwise)
    for a in allowed(DEPLOYER):
        assert not any(fnmatch.fnmatch(a, pat) or fnmatch.fnmatch(pat, a) for pat in ("sagemaker:*", "bedrock:*", "ec2:RunInstances", "eks:*", "ecs:*", "batch:*")), a


def test_deployer_cannot_touch_billing_identity_or_create_credentials_or_billable_extras():
    d = denied(DEPLOYER)
    for a in ("iam:CreateAccessKey", "iam:CreateUser", "organizations:*", "sso:*", "account:*", "ce:*", "ec2:CreateNatGateway", "ec2:AllocateAddress", "rds:Create*",
              "secretsmanager:CreateSecret", "xray:*", "cloudwatch:PutMetricData", "cloudwatch:PutMetricAlarm", "synthetics:*", "rum:*"):
        assert a in d, a


def test_deployer_has_no_blanket_allow():
    assert "*" not in allowed(DEPLOYER) and "*:*" not in allowed(DEPLOYER)
    assert all(a != "iam:*" for a in allowed(DEPLOYER))


def test_ops_reader_policy_is_exactly_the_read_calls_the_code_makes_and_nothing_else():
    assert allowed(READER) == READER_ACTIONS
    for a in allowed(READER):
        assert re.match(r"^(lambda:GetFunctionConfiguration|dynamodb:Describe\w+|cloudfront:GetDistribution|cognito-idp:DescribeUserPool|cloudwatch:GetMetricData)$", a)
    for s in READER["Statement"]:
        if "cloudwatch:GetMetricData" in s["Action"]:
            assert s["Resource"] == "*"                                                        # the only action AWS cannot scope; everything else names exact resources
        else:
            res = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
            assert all(r.startswith("arn:aws:") and "*" not in r for r in res), res
    assert not any(re.search(r"ce:|billing|budgets|iam:|organizations|resource-explorer|ec2:", a) for a in allowed(READER))


def test_ops_reader_policy_actions_match_the_calls_in_the_source():
    src = (ROOT / "apps/api/enigma_api/awsops.py").read_text()
    calls = set(re.findall(r"\.(get_function_configuration|describe_table|describe_time_to_live|get_distribution|describe_user_pool|get_metric_data)\(", src))
    assert calls == {"get_function_configuration", "describe_table", "describe_time_to_live", "get_distribution", "describe_user_pool", "get_metric_data"}
    assert not re.search(r"\.(put_|create_|update_|delete_|tag_|list_account|get_cost|get_dimension)\w*\(", src)


def test_resource_manifest_is_unapproved_and_blocks_every_chargeable_action():
    a = MANIFEST["approval"]
    assert a["approved"] is False and a["region"] is None and a["identity"] is None and a["dollar_ceiling_usd_per_month"] is None
    assert a["credit_expiry_checked_in_billing_console"] is None and a["free_tier_eligibility_checked"] is None
    assert a["budget_notification_only_not_a_cap"] is True
    assert MANIFEST["compute_boundary"]["aws_gpu"] == "forbidden" and "Kaggle" in MANIFEST["compute_boundary"]["inference_location"]


def test_resource_manifest_contains_only_control_plane_types_and_a_complete_teardown_order():
    ids = [r["id"] for r in MANIFEST["resources"]]
    assert len(ids) == len(set(ids))
    for r in MANIFEST["resources"]:
        assert not re.match(r"AWS::(EC2::Instance|EC2::NatGateway|SageMaker|Bedrock|ECS|EKS|Batch|RDS|ElasticLoadBalancing|Route53|SecretsManager)", r["type"]), r
    assert set(MANIFEST["teardown_order"]) == set(ids)
    assert MANIFEST["teardown_order"].index("dynamodb_parity_table") < MANIFEST["teardown_order"].index("dynamodb_table")        # throwaway table goes first
    assert MANIFEST["teardown_order"][-1] == "iam_roles" and MANIFEST["teardown_order"][0] == "cloudwatch_dashboard"
    assert next(r for r in MANIFEST["resources"] if r["id"] == "dynamodb_table")["gsi"] == 0
    assert next(r for r in MANIFEST["resources"] if r["id"] == "log_groups")["retention_days"] == 7
    assert next(r for r in MANIFEST["resources"] if r["id"] == "cloudwatch_dashboard")["metrics"] == 30


def test_example_ops_manifest_parses_with_the_strict_parser():
    m = Manifest.parse((ROOT / "infra/aws/manifest.example.json").read_text())
    assert m.ddb_table == "enigma-dev" and m.region == "us-east-1"


def test_dashboard_resource_matches_the_real_draft_file():
    d = json.loads((ROOT / "infra/aws/dashboards/enigma-control-plane.json").read_text())
    n = sum(len(w["properties"]["metrics"]) for w in d["widgets"] if w["type"] == "metric")
    assert n == next(r for r in MANIFEST["resources"] if r["id"] == "cloudwatch_dashboard")["metrics"]
