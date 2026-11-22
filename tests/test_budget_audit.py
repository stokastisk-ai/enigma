"""Budget audit: offline, synthetic AWS CLI responses. No AWS, no network, no GPU."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import enigma_aws_budget_audit as M  # noqa: E402

ACCT = "123456789012"
ARN = f"arn:aws:sts::{ACCT}:assumed-role/AWSReservedSSO_Deployer_abcd/me"
EMAIL = "someone@example.com"


def budget(**kw):
    b = {"BudgetName": M.BUDGET, "BudgetLimit": {"Amount": "20.0", "Unit": "USD"}, "BudgetType": "COST",
         "TimeUnit": "MONTHLY", "TimePeriod": {"Start": "2026-11-01T00:00:00+00:00", "End": "2087-06-15T00:00:00+00:00"},
         "CostTypes": {"IncludeCredit": False, "UseBlended": False, "UseAmortized": False},
         "BillingViewArn": f"arn:aws:billing::{ACCT}:billingview/primary",
         "CalculatedSpend": {"ActualSpend": {"Amount": "0.0"}, "ForecastedSpend": {"Amount": "0.0"}},
         "HealthStatus": {"Status": "HEALTHY"}}
    b.update(kw)
    return b


FULL = [{"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": t, "ThresholdType": "PERCENTAGE"}
        for t in (25.0, 50.0, 75.0, 100.0)]
FULL.append({"NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN", "Threshold": 100.0, "ThresholdType": "PERCENTAGE"})


def fake(budgets, notifs=FULL, subs=({"SubscriptionType": "EMAIL", "Address": EMAIL},), identity=None, errors=None):
    errors = errors or {}
    calls = []

    def run(*args):
        calls.append(args)
        key = args[1] if args[0] == "budgets" else "sts"
        if key in errors:
            return None, errors[key]
        if args[0] == "sts":
            return (identity if identity is not None else {"Arn": ARN, "Account": ACCT}), None
        if key == "describe-budgets":
            return {"Budgets": budgets}, None
        if key == "describe-notifications-for-budget":
            return {"Notifications": notifs}, None
        if key == "describe-subscribers-for-notification":
            return {"Subscribers": list(subs)}, None
        raise AssertionError(f"unexpected call {args}")
    run.calls = calls
    return run


def test_verified_full_policy_exit_0_and_read_only_calls():
    run = fake([budget()])
    status, res, _ = M.audit(run)
    assert status == "VERIFIED" and M.EXIT[status] == 0 and not res["warnings"]
    allowed = {"get-caller-identity", "describe-budgets", "describe-notifications-for-budget", "describe-subscribers-for-notification"}
    assert all(c[1] in allowed for c in run.calls)
    assert all(not c[1].startswith(("create", "update", "delete", "put", "execute")) for c in run.calls)


def test_missing_budget():
    status, _, _ = M.audit(fake([budget(BudgetName="Other")]))
    assert status == "MISSING" and M.EXIT[status] == 10


@pytest.mark.parametrize("patch", [
    {"BudgetLimit": {"Amount": "25.0", "Unit": "USD"}},
    {"BudgetLimit": {"Amount": "20.0", "Unit": "EUR"}},
    {"BudgetType": "USAGE"},
    {"TimeUnit": "QUARTERLY"},
    {"TimePeriod": {"Start": "2026-11-01T00:00:00+00:00", "End": "2026-12-31T00:00:00+00:00"}},
])
def test_misconfigured(patch):
    status, res, _ = M.audit(fake([budget(**patch)]))
    assert status == "MISCONFIGURED" and res["hard_failures"] and M.EXIT[status] == 11


def test_existing_account_shape_is_verified_with_warnings_not_strict():
    """The real budget: one ACTUAL 25% alert (no ThresholdType field) and IncludeCredit=true."""
    b = budget(CostTypes={"IncludeCredit": True, "UseBlended": False, "UseAmortized": False})
    run = fake([b], notifs=[{"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 25.0}])
    status, res, _ = M.audit(run)
    assert status == "VERIFIED"
    text = " | ".join(res["warnings"])
    assert "IncludeCredit=true" in text and "$10" in text and "$15" in text and "$20" in text
    assert res["notifications"][0]["threshold_usd"] == 5.0
    status, _, _ = M.audit(run, strict=True)
    assert status == "MISCONFIGURED"


def test_notification_without_subscribers_warns():
    _, res, _ = M.audit(fake([budget()], subs=()))
    assert any("NO subscribers" in w for w in res["warnings"])


def test_permission_failures_are_distinct():
    for where in ("sts", "describe-budgets"):
        status, _, _ = M.audit(fake([budget()], errors={where: "denied"}))
        assert status == "PERMISSION_DENIED" and M.EXIT[status] == 12
    status, _, _ = M.audit(fake([budget()], errors={"sts": "session"}))
    assert status == "UNVERIFIED" and M.EXIT[status] == 13


@pytest.mark.parametrize("arn", [
    f"arn:aws:iam::{ACCT}:root",
    f"arn:aws:iam::{ACCT}:user/someone",
    f"arn:aws:sts::{ACCT}:assumed-role/NotSso/x",
])
def test_non_sso_identity_refused(arn):
    status, _, _ = M.audit(fake([budget()], identity={"Arn": arn, "Account": ACCT}))
    assert status == "REFUSED" and M.EXIT[status] == 2


@pytest.mark.parametrize("profile", ["default", "root", " Default ", ""])
def test_default_or_root_profile_refused_before_any_call(profile):
    def boom(*a):
        raise AssertionError("must not call AWS")
    assert M.main(["--profile", profile, "--no-save"], runner=boom) == 2


def test_no_account_id_arn_or_email_in_output_or_saved_file(tmp_path, capsys):
    run = fake([budget(), budget(BudgetName=M.SIBLING_BUDGET, BudgetLimit={"Amount": "1.0", "Unit": "USD"})])
    rc = M.main(["--project", str(tmp_path)], runner=run)
    out = capsys.readouterr().out
    saved = (tmp_path / ".enigma-local" / "aws-budget-audit-private.json").read_text()
    for blob in (out, saved):
        assert ACCT not in blob and "arn:aws" not in blob and EMAIL not in blob
    assert rc == 0
    assert (tmp_path / ".enigma-local").stat().st_mode & 0o777 == 0o700
    assert json.loads(saved)["sibling_budget"]["present"] is True


def test_sanitize_scrubs_leaks():
    s = M.sanitize({"x": f"{ACCT} arn:aws:iam::{ACCT}:role/a {EMAIL}"}, ACCT)
    assert ACCT not in s and "arn:aws" not in s and EMAIL not in s


def test_runner_strips_ambient_credentials_and_pins_profile(monkeypatch):
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "{}", ""

    def fake_run(cmd, env, **kw):
        seen["cmd"], seen["env"] = cmd, env
        return R()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE")
    monkeypatch.setenv("AWS_PROFILE", "default")
    monkeypatch.setattr(M.subprocess, "run", fake_run)
    M.make_runner("enigma-deployer", "us-east-1")("sts", "get-caller-identity")
    assert "AWS_ACCESS_KEY_ID" not in seen["env"] and "AWS_PROFILE" not in seen["env"]
    assert seen["cmd"][seen["cmd"].index("--profile") + 1] == "enigma-deployer"
    assert seen["cmd"][seen["cmd"].index("--region") + 1] == "us-east-1"


def test_cli_failure_classification_never_returns_raw_text():
    assert M.classify_cli_failure("An error occurred (AccessDeniedException) ... 123456789012") == "denied"
    assert M.classify_cli_failure("The SSO session has expired") == "session"
    assert M.classify_cli_failure("boom") == "other"
