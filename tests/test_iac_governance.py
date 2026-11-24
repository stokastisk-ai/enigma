"""Governance of the remaining AWS policy/IaC artifacts (IAM policies, DynamoDB templates, dormant AWS Ops page inputs): nothing GPU, least privilege, and the policies match what the code really calls."""
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


def test_example_ops_manifest_parses_with_the_strict_parser():
    m = Manifest.parse((ROOT / "infra/aws/manifest.example.json").read_text())
    assert m.ddb_table == "enigma-dev" and m.region == "us-east-1"


def test_ops_dashboard_file_is_valid_and_stays_within_the_free_metric_allowance():
    d = json.loads((ROOT / "infra/aws/dashboards/enigma-control-plane.json").read_text())
    n = sum(len(w["properties"]["metrics"]) for w in d["widgets"] if w["type"] == "metric")
    assert 0 < n <= 50


class _CfnLoader(yaml.SafeLoader):
    pass


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _all_templates():
    return sorted(p for p in (ROOT / "infra/aws").glob("*.yaml") if "AWSTemplateFormatVersion" in p.read_text())


def test_only_the_dynamodb_templates_remain_and_none_creates_billed_extras():
    """Hosting templates (Lambda/Amplify/CloudFront) were retired with the move to Railway; what is left must stay minimal and cheap."""
    names = {p.name for p in _all_templates()}
    assert names == {"dynamodb.yaml", "dynamodb-parity.yaml"}, names
    banned = re.compile(r"ProvisionedConcurrency|AWS::WAF|AWS::CloudFront::|AWS::Lambda::|AWS::CloudWatch::(Alarm|CompositeAlarm|InsightRule|AnomalyDetector)|AWS::Logs::MetricFilter|"
                        r"AWS::Synthetics|AWS::EC2::|AWS::RDS::|AWS::ElasticLoadBalancing|AWS::Events::Rule|AWS::Scheduler|AWS::SecretsManager|AWS::KMS::Key|"
                        r"AWS::ApplicationAutoScaling|AWS::StepFunctions|AWS::SQS|AWS::Kinesis|AWS::Route53|AWS::XRay|TracingConfig")
    for p in _all_templates():
        hits = banned.findall(p.read_text())
        assert not hits, f"{p.name}: {hits}"


def test_dynamodb_defaults_to_on_demand_without_pitr_and_log_groups_are_short_lived():
    for p in _all_templates():
        t = yaml.load(p.read_text(), Loader=_CfnLoader)
        for r in t["Resources"].values():
            if r["Type"] == "AWS::Logs::LogGroup":
                assert 1 <= r["Properties"]["RetentionInDays"] <= 14
            if r["Type"] == "AWS::DynamoDB::Table":
                if p.name == "dynamodb-parity.yaml":      # throwaway table, deleted right after the parity run: capacity must stay inside the 25/25 allowance
                    assert r["Properties"]["BillingMode"] == "PROVISIONED" and t["Parameters"]["ReadCapacity"]["MaxValue"] <= 25 and t["Parameters"]["WriteCapacity"]["MaxValue"] <= 25
                else:
                    assert t["Parameters"]["BillingMode"]["Default"] == "PAY_PER_REQUEST"
                assert r["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is False
