"""aws_teardown.py against a FAKE aws runner: no AWS call, no network, no credentials."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aws_teardown as T  # noqa: E402

APPROVED = "approval:\n  approved: true\n  region: us-east-1\n"
NOT_APPROVED = "approval:\n  approved: false\n  region: null\n"


class FakeAws:
    def __init__(self, arn="arn:aws:sts::1:assumed-role/AWSReservedSSO_EnigmaDeployer/x", bucket_objects=False):
        self.arn, self.calls, self.deleted, self.bucket_objects = arn, [], set(), bucket_objects
        self.stacks = {"enigma-dev": "ok", "enigma-untagged": "no", "other-app": "ok"}

    def __call__(self, argv):
        self.calls.append(argv)
        assert "--profile" in argv and "--region" in argv                       # every call is explicit
        k = tuple(argv[:2])
        if k == ("sts", "get-caller-identity"):
            return 0, json.dumps({"Arn": self.arn})
        if k == ("cloudformation", "describe-stacks"):
            return 0, json.dumps({"Stacks": [{"StackName": n, "StackStatus": "CREATE_COMPLETE", "Tags": [{"Key": "Project", "Value": "enigma"}] if v == "ok" else []}
                                             for n, v in self.stacks.items() if n not in self.deleted]})
        if k == ("cloudformation", "describe-stack-resources"):
            return 0, json.dumps({"StackResources": [{"ResourceType": "AWS::S3::Bucket", "LogicalResourceId": "B", "PhysicalResourceId": "enigma-zips"}]})
        if k == ("s3api", "list-objects-v2"):
            return 0, json.dumps({"Contents": [{"Key": "a.zip"}]} if self.bucket_objects else {})
        if k == ("cloudformation", "delete-stack"):
            self.deleted.add(argv[argv.index("--stack-name") + 1])
            return 0, ""
        if k == ("cloudformation", "wait"):
            return 0, ""
        if k[0] in ("lambda", "dynamodb", "logs", "amplify", "ec2", "rds", "elbv2", "sagemaker"):
            return 0, json.dumps({})
        if k[0] == "s3":
            return 0, ""
        raise AssertionError(argv)


def test_dry_run_lists_only_tagged_enigma_stacks_and_deletes_nothing():
    fake = FakeAws()
    assert T.main(["--profile", "p", "--region", "us-east-1"], run=fake) == 0
    assert not any(c[:2] == ["cloudformation", "delete-stack"] for c in fake.calls) and not fake.deleted


@pytest.mark.parametrize("arn", ["arn:aws:iam::1:root", "arn:aws:iam::1:user/bob"])
def test_root_and_iam_user_identities_are_refused(arn):
    with pytest.raises(SystemExit):
        T.Teardown("p", "us-east-1", FakeAws(arn=arn)).plan()


def test_explicit_profile_and_region_are_mandatory():
    with pytest.raises(SystemExit):
        T.Teardown("", "us-east-1", FakeAws())


def test_execute_refuses_without_manifest_approval_or_wrong_region():
    for text, region in ((NOT_APPROVED, "us-east-1"), (APPROVED, "eu-west-1")):
        fake = FakeAws()
        with pytest.raises(SystemExit):
            T.Teardown("p", region, fake).execute(["enigma-dev"], False, text)
        assert not fake.deleted


def test_execute_refuses_unconfirmed_untagged_or_foreign_stacks():
    for confirm in ([], ["enigma-untagged"], ["other-app"]):
        fake = FakeAws()
        with pytest.raises(SystemExit):
            T.Teardown("p", "us-east-1", fake).execute(confirm, False, APPROVED)
        assert not fake.deleted


def test_non_empty_bucket_blocks_unless_explicitly_allowed():
    fake = FakeAws(bucket_objects=True)
    with pytest.raises(SystemExit):
        T.Teardown("p", "us-east-1", fake).execute(["enigma-dev"], False, APPROVED)
    assert not fake.deleted
    assert T.Teardown("p", "us-east-1", fake, out=lambda *_: None).execute(["enigma-dev"], True, APPROVED) == 0
    assert fake.deleted == {"enigma-dev"} and any(c[0] == "s3" for c in fake.calls)


def test_confirmed_delete_reports_clean_residue():
    fake = FakeAws()
    assert T.Teardown("p", "us-east-1", fake, out=lambda *_: None).execute(["enigma-dev"], False, APPROVED) == 0
    assert fake.deleted == {"enigma-dev"}


def test_the_shipped_manifest_is_not_approved():
    assert T.Teardown.approval_ok(T.MANIFEST.read_text(), "us-east-1") is False


# ---- incomplete deletion and unexpected resources (synthetic responses) ----
class FakeAws2(FakeAws):
    def __init__(self, *a, wait_rc=0, leftover_lambda=False, stack_resources=None, sweep=None, delete_failed=False, deny=(), **k):
        super().__init__(*a, **k)
        self.wait_rc, self.leftover_lambda, self.sweep, self.delete_failed, self.deny = wait_rc, leftover_lambda, sweep or {}, delete_failed, set(deny)
        self.res = stack_resources

    def __call__(self, argv):
        k = tuple(argv[:2])
        if k[0] in self.deny:
            self.calls.append(argv)
            return 255, "AccessDenied"
        if k == ("cloudformation", "wait"):
            self.calls.append(argv)
            if self.wait_rc:
                self.deleted.discard(argv[argv.index("--stack-name") + 1]) if not self.delete_failed else None
            return self.wait_rc, "Waiter failed" if self.wait_rc else ""
        if k == ("cloudformation", "describe-stack-resources") and self.res is not None:
            self.calls.append(argv)
            return 0, json.dumps({"StackResources": self.res})
        if k == ("cloudformation", "describe-stacks") and self.delete_failed and self.deleted:
            self.calls.append(argv)
            return 0, json.dumps({"Stacks": [{"StackName": "enigma-dev", "StackStatus": "DELETE_FAILED", "Tags": [{"Key": "Project", "Value": "enigma"}]}]})
        if k == ("lambda", "list-functions") and self.leftover_lambda:
            self.calls.append(argv)
            return 0, json.dumps({"Functions": [{"FunctionName": "enigma-api-dev"}]})
        for key, body in self.sweep.items():
            if k[:1] == (key[0],) and k[1] == key[1]:
                self.calls.append(argv)
                return 0, json.dumps(body)
        if k[0] in ("ec2", "rds", "elbv2", "sagemaker"):
            self.calls.append(argv)
            return 0, json.dumps({})
        return super().__call__(argv)


def test_incomplete_deletion_is_reported_as_a_failure_not_success():
    fake = FakeAws2(wait_rc=255)
    msgs: list[str] = []
    assert T.Teardown("p", "us-east-1", fake, out=msgs.append).execute(["enigma-dev"], False, APPROVED) == 1
    assert any("CRITICAL" in m and "not confirmed" in m for m in msgs)


def test_delete_failed_stack_and_leftover_lambda_show_up_as_residue_exit_2():
    fake = FakeAws2(delete_failed=True, leftover_lambda=True)
    msgs: list[str] = []
    assert T.Teardown("p", "us-east-1", fake, out=msgs.append).execute(["enigma-dev"], False, APPROVED) == 2
    assert "stack:enigma-dev(DELETE_FAILED)" in msgs[-2] and "lambda:enigma-api-dev" in msgs[-2]


def test_stack_with_forbidden_resource_types_is_blocked_until_acknowledged():
    res = [{"ResourceType": "AWS::EC2::NatGateway", "LogicalResourceId": "Nat", "PhysicalResourceId": "nat-1"}, {"ResourceType": "AWS::Lambda::Function", "LogicalResourceId": "F", "PhysicalResourceId": "f"}]
    fake = FakeAws2(stack_resources=res)
    plan = T.Teardown("p", "us-east-1", fake).plan()
    assert plan[0]["unexpected"] == [("AWS::EC2::NatGateway", "Nat")]
    with pytest.raises(SystemExit):
        T.Teardown("p", "us-east-1", fake).execute(["enigma-dev"], False, APPROVED)
    assert not fake.deleted
    assert T.Teardown("p", "us-east-1", fake, out=lambda *_: None).execute(["enigma-dev"], False, APPROVED, allow_unexpected=True) in (0, 2)
    assert fake.deleted == {"enigma-dev"}


def test_sweep_reports_billable_lookalikes_and_never_deletes_them():
    fake = FakeAws2(sweep={("ec2", "describe-nat-gateways"): {"NatGateways": [{"NatGatewayId": "nat-9"}]},
                           ("rds", "describe-db-instances"): {"DBInstances": [{"DBInstanceIdentifier": "db1"}]},
                           ("sagemaker", "list-endpoints"): {"Endpoints": [{"EndpointName": "ep"}]}})
    msgs: list[str] = []
    rc = T.Teardown("p", "us-east-1", fake, out=msgs.append).execute(["enigma-dev"], False, APPROVED)
    assert rc == 2 and all(x in msgs[-1] for x in ("nat-gateway:nat-9", "rds-instance:db1", "sagemaker-endpoint:ep"))
    assert not any(c[1].startswith(("delete", "terminate")) for c in fake.calls if c[0] in ("ec2", "rds", "sagemaker"))


def test_denied_sweep_is_reported_as_unchecked_not_clean():
    sweep = T.Teardown("p", "us-east-1", FakeAws2(deny=("rds",))).billable_sweep()
    assert "unchecked:rds-instance" in sweep
