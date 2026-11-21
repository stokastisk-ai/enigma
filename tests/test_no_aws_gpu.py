"""Repository audit (skills.md section 16): AWS hosts ONLY the CPU control plane. Kaggle 2x T4 is the only GPU.

Fails if any IaC, Lambda package, backend, web code or env template introduces an AWS GPU/accelerator resource, a hosted-AI fallback, a CUDA
library in the Lambda package, or a NAT/EC2/ECS/EKS/Batch resource. Documentation is exempt (it must be able to say what is forbidden).
Also parses both IaC templates (offline, no AWS call) so syntax/shape regressions are caught without any AWS access.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "aws"

FORBIDDEN_TYPES = re.compile(r"^AWS::(SageMaker|Bedrock|BedrockAgent|Batch|EKS|ECS|ElasticLoadBalancing|ElasticLoadBalancingV2|AutoScaling|EC2::(Instance|NatGateway|EIP|LaunchTemplate|SpotFleet|Host|CapacityReservation))")
GPU_INSTANCE = re.compile(r"\b(p[2-6][a-z]*|g[3-7][a-z]*|inf[12]|trn[12]|dl[12][a-z]*|f[12]|vt1|ml)\.(\d*x?large|metal|\d*xlarge|nano|micro|small|medium)\b", re.I)
BAD_REQS = re.compile(r"^\s*(torch|torchvision|vllm|stokastisk|nvidia[-_\w]*|pynvml|cuda[-_\w]*|triton|openai|anthropic|google-generativeai|google-genai|cohere|mistralai|transformers)\b", re.I | re.M)
BAD_IMPORTS = {"torch", "vllm", "pynvml", "stokastisk", "openai", "anthropic", "cohere", "mistralai", "vertexai", "transformers", "tensorflow", "cupy", "pycuda"}
AI_ENDPOINTS = re.compile(r"api\.openai\.com|generativelanguage\.googleapis|api\.anthropic\.com|bedrock-runtime|bedrock\.|sagemaker-runtime|runtime\.sagemaker", re.I)
AI_ENV = re.compile(r"^\s*#?\s*(OPENAI|ANTHROPIC|GOOGLE_GENERATIVE|GEMINI|COHERE|MISTRAL|BEDROCK|SAGEMAKER)[A-Z_]*=", re.I | re.M)


class _L(yaml.SafeLoader):
    pass


_L.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
                         else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))


def _templates():
    return sorted(INFRA.glob("*.yaml"))


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_parses_and_contains_no_forbidden_resources(path):
    doc = yaml.load(path.read_text(), Loader=_L)
    assert doc["AWSTemplateFormatVersion"] == "2010-09-09" and doc["Resources"]
    for name, res in doc["Resources"].items():
        assert not FORBIDDEN_TYPES.match(res["Type"]), f"{path.name}:{name} uses forbidden resource type {res['Type']}"
        assert {"Type", "Properties"} <= set(res) or res["Type"] == "AWS::Logs::LogGroup"
    assert not GPU_INSTANCE.search(path.read_text()), "GPU / accelerator instance type found in IaC"
    for k, v in _walk(doc):
        if k == "Runtime":
            assert v in ("python3.13", "nodejs24.x", "nodejs22.x"), f"unexpected Lambda runtime {v}"
        if k in ("Architectures",):
            assert set(v) <= {"x86_64", "arm64"}


def test_lambda_function_is_cpu_only_and_python_313():
    doc = yaml.load((INFRA / "template.yaml").read_text(), Loader=_L)
    fn = doc["Resources"]["ApiFunction"]["Properties"]
    assert fn["Runtime"] == "python3.13" and fn["ReservedConcurrentExecutions"] <= 5 and "Layers" not in fn
    assert not any(r["Type"] == "AWS::Lambda::Function" and "Image" in str(r["Properties"].get("PackageType", "")) for r in doc["Resources"].values())


def test_dynamodb_template_has_no_gsi_and_a_least_privilege_policy():
    doc = yaml.load((INFRA / "dynamodb.yaml").read_text(), Loader=_L)
    t = doc["Resources"]["Table"]["Properties"]
    assert "GlobalSecondaryIndexes" not in t and t["TimeToLiveSpecification"]["AttributeName"] == "ttl"
    stmts = doc["Resources"]["ApiAccessPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    allow = next(s for s in stmts if s["Effect"] == "Allow")
    assert not {"dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:UpdateTable", "dynamodb:*", "*"} & set(allow["Action"])
    assert any(s["Effect"] == "Deny" and "dynamodb:DeleteItem" in s["Action"] for s in stmts)       # audit rows append-only


def test_lambda_requirements_contain_no_gpu_or_ai_provider_packages():
    for f in ("requirements-aws.txt", "requirements.txt"):
        txt = "\n".join(l for l in (ROOT / "apps" / "api" / f).read_text().splitlines() if not l.lstrip().startswith("#"))
        assert not BAD_REQS.search(txt), f"{f} lists a GPU/CUDA/AI-provider package"
    assert "psycopg" in (ROOT / "apps/api/requirements-aws.txt").read_text()


def test_backend_never_imports_gpu_libs_or_calls_ai_providers():
    for py in list((ROOT / "apps" / "api").rglob("*.py")):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not {m.split(".")[0] for m in mods} & BAD_IMPORTS, f"{py.relative_to(ROOT)} imports a GPU/AI library"
        assert not AI_ENDPOINTS.search(py.read_text()), f"{py.relative_to(ROOT)} references a hosted AI endpoint"
        assert not re.search(r"client\(\s*['\"](bedrock|sagemaker|bedrock-runtime|sagemaker-runtime|ec2)['\"]", py.read_text()), f"{py.relative_to(ROOT)} creates a forbidden AWS client"


def test_web_app_has_no_hosted_ai_fallback():
    web = ROOT / "apps" / "web"
    for f in [*web.glob("app/**/*.ts*"), *web.glob("lib/**/*.ts*"), *web.glob("components/**/*.ts*"), *web.glob("hooks/**/*.ts*"), web / "proxy.ts"]:
        assert not AI_ENDPOINTS.search(f.read_text()), f"{f.relative_to(ROOT)} references a hosted AI endpoint"


def test_env_template_declares_no_ai_provider_keys():
    assert not AI_ENV.search((ROOT / ".env.example").read_text())


def test_only_the_connector_may_touch_gpu_libraries_and_it_is_guarded():
    # NVML lives in the Kaggle-side connector only, and refuses to run when the local-GPU guard is set (tests/conftest.py sets it)
    gpu = (ROOT / "connector/enigma_connector/gpu.py").read_text()
    assert "ENIGMA_FORBID_LOCAL_GPU" in gpu and "import pynvml" in gpu
    assert not any("pynvml" in p.read_text() for p in (ROOT / "apps").rglob("*.py"))


# ---- the detectors themselves must be able to fail
@pytest.mark.parametrize("bad", ["p4d.24xlarge", "p5.48xlarge", "g5.xlarge", "g4dn.xlarge", "g6e.12xlarge", "inf2.xlarge", "trn1.2xlarge", "dl1.24xlarge", "ml.g5.xlarge", "f1.2xlarge"])
def test_gpu_instance_detector_catches_gpu_and_accelerator_families(bad):
    assert GPU_INSTANCE.search(f"InstanceType: {bad}")


@pytest.mark.parametrize("ok", ["db.t4g.micro", "db.t3.micro", "m7g.large", "c7g.xlarge", "t4g.micro", "t3.micro", "r6g.large"])
def test_gpu_instance_detector_does_not_flag_cpu_instances(ok):
    assert not GPU_INSTANCE.search(f"InstanceType: {ok}")


@pytest.mark.parametrize("t", ["AWS::SageMaker::Endpoint", "AWS::Bedrock::Agent", "AWS::EC2::Instance", "AWS::EC2::NatGateway", "AWS::ECS::Cluster", "AWS::EKS::Cluster",
                               "AWS::Batch::JobQueue", "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::EC2::EIP"])
def test_forbidden_resource_types_are_detected(t):
    assert FORBIDDEN_TYPES.match(t)


@pytest.mark.parametrize("t", ["AWS::Lambda::Function", "AWS::DynamoDB::Table", "AWS::Logs::LogGroup", "AWS::IAM::Role", "AWS::Cognito::UserPool", "AWS::CloudFront::Distribution"])
def test_control_plane_resource_types_are_allowed(t):
    assert not FORBIDDEN_TYPES.match(t)


def test_requirements_and_env_detectors_fire():
    assert BAD_REQS.search("torch==2.9\n") and BAD_REQS.search("nvidia-ml-py==13\n") and BAD_REQS.search("vllm\n") and not BAD_REQS.search("fastapi==0.163.1\nboto3==1.44.2\n")
    assert AI_ENV.search("OPENAI_API_KEY=\n") and AI_ENV.search("# GEMINI_API_KEY=\n") and not AI_ENV.search("ENIGMA_STORE=dynamodb\n")
