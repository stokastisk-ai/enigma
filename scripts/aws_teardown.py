"""Enigma AWS teardown. DRY-RUN BY DEFAULT. Never run `--execute` without the owner's separate written approval.

    /usr/local/bin/python3.13 scripts/aws_teardown.py --profile enigma-deployer --region us-east-1            # inventory + plan, read-only
    /usr/local/bin/python3.13 scripts/aws_teardown.py --profile enigma-deployer --region us-east-1 --execute \
        --confirm-stack enigma-dev --confirm-stack enigma-web-dev                                               # destructive

Safety rules (all enforced in code and covered by tests/test_aws_teardown.py):
  * every AWS call carries an explicit --profile and --region; the identity must be a NON-ROOT assumed role (never root, never an IAM user key);
  * only CloudFormation stacks whose name starts with `enigma-` AND that carry the tag Project=enigma are ever considered;
  * `--execute` needs the manifest's approval block filled in (approved: true, region equal to --region) and every stack named explicitly with --confirm-stack;
  * a stack that owns a non-empty S3 bucket is refused unless --empty-artifact-bucket is given (the bucket only holds Lambda zips);
  * nothing is force-deleted and nothing outside the tagged stacks is touched; leftovers are reported, never guessed at;
  * after deletion the script re-lists stacks, Lambda functions, DynamoDB tables and log groups and reports any enigma-* residue (exit code 2).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "infra" / "aws" / "resource-manifest.yaml"
PREFIX = "enigma-"
Runner = Callable[[list[str]], tuple[int, str]]


def real_runner(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(["aws", *argv], capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout if p.returncode == 0 else p.stderr[:500]


class Teardown:
    def __init__(self, profile: str, region: str, run: Runner = real_runner, out=print):
        if not profile or not region:
            raise SystemExit("explicit --profile and --region are required")
        self.profile, self.region, self.run, self.out = profile, region, run, out

    def aws(self, *args: str) -> dict:
        rc, text = self.run([*args, "--profile", self.profile, "--region", self.region, "--output", "json"])
        if rc != 0:
            raise RuntimeError(f"aws {' '.join(args[:2])} failed: {text.strip()[:200]}")
        return json.loads(text) if text.strip() else {}

    # ---------------------------------------------------------------- guards
    def require_non_root(self) -> None:
        arn = self.aws("sts", "get-caller-identity").get("Arn", "")
        if ":root" in arn or ":assumed-role/" not in arn:
            raise SystemExit("refusing: identity is not a non-root assumed role (root keys and IAM-user keys are never used)")

    @staticmethod
    def approval_ok(manifest_text: str, region: str) -> bool:
        flat = {ln.split(":", 1)[0].strip(): ln.split(":", 1)[1].split("#")[0].strip() for ln in manifest_text.splitlines() if ":" in ln and not ln.lstrip().startswith(("-", "#"))}
        return flat.get("approved") == "true" and flat.get("region", "null") == region

    # ---------------------------------------------------------------- inventory
    def stacks(self) -> list[dict]:
        out = []
        for s in self.aws("cloudformation", "describe-stacks").get("Stacks", []):
            tags = {t["Key"]: t["Value"] for t in s.get("Tags", [])}
            if s["StackName"].startswith(PREFIX) and tags.get("Project", "").lower() == "enigma" and s["StackStatus"] != "DELETE_COMPLETE":
                out.append(s)
        return out

    def resources(self, stack: str) -> list[dict]:
        return self.aws("cloudformation", "describe-stack-resources", "--stack-name", stack).get("StackResources", [])

    def residue(self) -> list[str]:
        found = [f"stack:{s['StackName']}" for s in self.stacks()]
        found += [f"lambda:{f['FunctionName']}" for f in self.aws("lambda", "list-functions").get("Functions", []) if f["FunctionName"].startswith(PREFIX)]
        found += [f"dynamodb:{t}" for t in self.aws("dynamodb", "list-tables").get("TableNames", []) if t.startswith(PREFIX)]
        found += [f"logs:{g['logGroupName']}" for g in self.aws("logs", "describe-log-groups", "--log-group-name-prefix", "/aws/lambda/" + PREFIX).get("logGroups", [])]
        found += [f"amplify:{a['name']}" for a in self.aws("amplify", "list-apps").get("apps", []) if a["name"].startswith(PREFIX)]
        return found

    def plan(self) -> list[dict]:
        self.require_non_root()
        plan = []
        for s in self.stacks():
            res = self.resources(s["StackName"])
            plan.append({"stack": s["StackName"], "status": s["StackStatus"], "resources": [(r["ResourceType"], r["LogicalResourceId"]) for r in res],
                         "buckets": [r["PhysicalResourceId"] for r in res if r["ResourceType"] == "AWS::S3::Bucket" and r.get("PhysicalResourceId")]})
        return plan

    def bucket_empty(self, bucket: str) -> bool:
        return not self.aws("s3api", "list-objects-v2", "--bucket", bucket, "--max-items", "1").get("Contents")

    # ---------------------------------------------------------------- execute
    def execute(self, confirm: list[str], empty_bucket: bool, manifest_text: str) -> int:
        if not self.approval_ok(manifest_text, self.region):
            raise SystemExit("refusing: infra/aws/resource-manifest.yaml approval block is not approved for this region")
        plan = self.plan()
        names = {p["stack"] for p in plan}
        if not confirm or set(confirm) - names:
            raise SystemExit(f"refusing: every --confirm-stack must name an existing tagged enigma stack; known: {sorted(names)}")
        for p in plan:
            if p["stack"] not in confirm:
                continue
            for b in p["buckets"]:
                if not self.bucket_empty(b):
                    if not empty_bucket:
                        raise SystemExit(f"refusing: bucket {b} is not empty (Lambda zips only); pass --empty-artifact-bucket to remove its objects")
                    self.run(["s3", "rm", f"s3://{b}", "--recursive", "--profile", self.profile, "--region", self.region])
            self.out(f"deleting stack {p['stack']} ...")
            self.aws("cloudformation", "delete-stack", "--stack-name", p["stack"])
            rc, text = self.run(["cloudformation", "wait", "stack-delete-complete", "--stack-name", p["stack"], "--profile", self.profile, "--region", self.region])
            if rc != 0:
                self.out(f"CRITICAL: deletion of {p['stack']} not confirmed: {text.strip()[:200]}")
                return 1
        left = self.residue()
        self.out("residue: " + (", ".join(left) if left else "none"))
        return 2 if left else 0


def main(argv: list[str] | None = None, run: Runner = real_runner) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--execute", action="store_true", help="DESTRUCTIVE. Default is a read-only plan.")
    ap.add_argument("--confirm-stack", action="append", default=[])
    ap.add_argument("--empty-artifact-bucket", action="store_true")
    a = ap.parse_args(argv)
    t = Teardown(a.profile, a.region, run)
    if a.execute:
        return t.execute(a.confirm_stack, a.empty_artifact_bucket, MANIFEST.read_text())
    plan = t.plan()
    print(json.dumps({"mode": "DRY-RUN (nothing deleted)", "plan": plan, "residue_now": t.residue()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
