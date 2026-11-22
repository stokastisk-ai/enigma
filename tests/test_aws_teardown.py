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
        if k[0] in ("lambda", "dynamodb", "logs", "amplify"):
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
