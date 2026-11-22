#!/usr/bin/env python3.13
"""Read-only Enigma AWS budget and non-root identity audit.

Requires AWS CLI v2 and an already configured IAM Identity Center profile. It performs ONLY
sts:GetCallerIdentity and budgets:Describe* calls. It never creates, updates or deletes anything,
never touches the default credential chain, root, GPUs or Kaggle, and never prints or stores the
AWS account ID, ARNs, tokens or e-mail addresses.

Exit codes (stable, for CI / deployment preflight):
  0  VERIFIED            budget exactly as required (warnings allowed unless --strict)
  2  REFUSED             unsafe profile / identity is not an Identity Center assumed role
  10 MISSING             the named budget does not exist
  11 MISCONFIGURED       budget exists but name/currency/amount/type/period/recurrence differ
                         (or, with --strict, a recommendation warning is open)
  12 PERMISSION_DENIED   AWS refused the read (missing budgets:* / billing rights)
  13 UNVERIFIED          expired session, timeout, CLI missing, invalid output
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

PROJECT = Path(__file__).resolve().parents[1]
BUDGET = 'Enigma-Monthly-USD20'
SIBLING_BUDGET = 'My Zero-Spend Budget'
REGION = 'us-east-1'
TARGET_USD = 20.0
RECOMMENDED_ACTUAL_USD = (5.0, 10.0, 15.0, 20.0)
IDENTITY_ARN = re.compile(r'^arn:aws[a-z-]*:sts::\d{12}:assumed-role/AWSReservedSSO_[^/]+/[^/]+$')
FORBIDDEN_PROFILES = {'', 'default', 'root'}

EXIT = {'VERIFIED': 0, 'REFUSED': 2, 'MISSING': 10, 'MISCONFIGURED': 11,
        'PERMISSION_DENIED': 12, 'UNVERIFIED': 13}

Runner = Callable[..., tuple[Any, str | None]]


def classify_cli_failure(stderr: str) -> str:
    """Map AWS CLI stderr to a coarse kind; the text itself is never shown or stored."""
    s = stderr.lower()
    if 'accessdenied' in s or 'not authorized' in s or 'unauthorizedoperation' in s \
            or 'iam user access not activated' in s:
        return 'denied'
    if 'expired' in s or 'sso' in s or 'token' in s or 'credentials' in s:
        return 'session'
    if 'notfound' in s:
        return 'notfound'
    return 'other'


def make_runner(profile: str, region: str) -> Runner:
    """Return a runner bound to an explicit profile+region. Never uses ambient credentials."""
    env = os.environ.copy()
    for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                'AWS_SECURITY_TOKEN', 'AWS_PROFILE', 'AWS_DEFAULT_PROFILE', 'AWS_ROLE_ARN',
                'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_DEFAULT_REGION', 'AWS_REGION',
                'AWS_CONTAINER_CREDENTIALS_FULL_URI', 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'):
        env.pop(key, None)
    env['AWS_PAGER'] = ''
    env['AWS_EC2_METADATA_DISABLED'] = 'true'

    def run(*args: str) -> tuple[Any, str | None]:
        command = ['aws', '--no-cli-pager', '--profile', profile, '--region', region,
                   *args, '--output', 'json']
        try:
            r = subprocess.run(command, env=env, capture_output=True, text=True,
                               timeout=35, check=False)
        except FileNotFoundError:
            return None, 'cli_missing'
        except (OSError, subprocess.TimeoutExpired):
            return None, 'timeout'
        if r.returncode:
            return None, classify_cli_failure(r.stderr or '')
        try:
            return json.loads(r.stdout), None
        except json.JSONDecodeError:
            return None, 'bad_json'
    return run


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _recurring(budget: dict) -> bool:
    end = (budget.get('TimePeriod') or {}).get('End', '')
    m = re.match(r'(\d{4})-', str(end))
    return budget.get('TimeUnit') == 'MONTHLY' and bool(m) and int(m.group(1)) >= 2050


def check_budget(budget: dict, expected_name: str, expected_usd: float) -> tuple[dict, list[str]]:
    """Strict checks of the budget body. Returns (sanitized facts, hard failures)."""
    limit = budget.get('BudgetLimit') or {}
    amount = _num(limit.get('Amount'))
    ct = budget.get('CostTypes') or {}
    facts = {
        'name': budget.get('BudgetName'),
        'type': budget.get('BudgetType'),
        'time_unit': budget.get('TimeUnit'),
        'currency': limit.get('Unit'),
        'amount': amount,
        'recurring_monthly': _recurring(budget),
        'include_credit': ct.get('IncludeCredit'),
        'include_refund': ct.get('IncludeRefund'),
        'include_tax': ct.get('IncludeTax'),
        'use_blended': ct.get('UseBlended'),
        'use_amortized': ct.get('UseAmortized'),
        'health': (budget.get('HealthStatus') or {}).get('Status'),
        'actual_spend': _num(((budget.get('CalculatedSpend') or {}).get('ActualSpend') or {}).get('Amount')),
        'forecast_spend': _num(((budget.get('CalculatedSpend') or {}).get('ForecastedSpend') or {}).get('Amount')),
    }
    bad = []
    if facts['name'] != expected_name:
        bad.append('name differs')
    if facts['type'] != 'COST':
        bad.append(f"type is {facts['type']!r}, expected COST")
    if facts['currency'] != 'USD':
        bad.append(f"currency is {facts['currency']!r}, expected USD")
    if amount is None or abs(amount - expected_usd) > 0.001:
        bad.append(f'amount is {amount}, expected {expected_usd}')
    if facts['time_unit'] != 'MONTHLY':
        bad.append(f"period is {facts['time_unit']!r}, expected MONTHLY")
    elif not facts['recurring_monthly']:
        bad.append('budget is not recurring (its end date is near, not open-ended)')
    return facts, bad


def check_notifications(notifs: list[dict], limit_usd: float, subscribers: list[list[dict]]) -> tuple[list[dict], list[str]]:
    """Summarise thresholds (no addresses) and derive recommendation warnings."""
    out, warnings = [], []
    actual_usd: list[float] = []
    for n, subs in zip(notifs, subscribers):
        ttype = n.get('ThresholdType') or 'PERCENTAGE'   # AWS omits the field for the default
        thr = _num(n.get('Threshold'))
        usd = None
        if thr is not None:
            usd = round(thr * limit_usd / 100, 4) if ttype == 'PERCENTAGE' else thr
        kinds: dict[str, int] = {}
        for s in subs:
            k = s.get('SubscriptionType', 'UNKNOWN')
            kinds[k] = kinds.get(k, 0) + 1
        item = {'notification_type': n.get('NotificationType'),
                'comparison': n.get('ComparisonOperator'),
                'threshold': thr, 'threshold_type': ttype, 'threshold_usd': usd,
                'state': n.get('NotificationState'),
                'subscriber_count': len(subs), 'subscriber_kinds': kinds,
                'subscription_status': 'not exposed by the Budgets API (e-mail subscribers need no confirmation)'}
        out.append(item)
        if n.get('NotificationType') == 'ACTUAL' and usd is not None:
            actual_usd.append(usd)
        if not subs:
            warnings.append(f"notification at {thr} {ttype} has NO subscribers")
    if not out:
        warnings.append('no notifications configured: spend would go unnoticed')
    for want in RECOMMENDED_ACTUAL_USD:
        if not any(abs(u - want) < 0.01 for u in actual_usd):
            warnings.append(f'recommended ACTUAL alert at ${want:g} is not configured')
    if not any(i['notification_type'] == 'FORECASTED' for i in out):
        warnings.append('no FORECASTED alert configured (optional early warning)')
    return out, warnings


def audit(run: Runner, *, budget_name: str = BUDGET, sibling: str | None = SIBLING_BUDGET,
          expected_usd: float = TARGET_USD, strict: bool = False) -> tuple[str, dict, list[str]]:
    """Pure orchestration over an injected runner. Returns (status, sanitized_result, console_lines)."""
    lines: list[str] = []
    result: dict[str, Any] = {'checked_utc': datetime.now(timezone.utc).isoformat(),
                              'budget_name': budget_name, 'expected_usd': expected_usd,
                              'note': 'AWS Budgets are asynchronous notifications, NOT spending limits.',
                              'writes_executed': 0}

    def finish(status: str) -> tuple[str, dict, list[str]]:
        result['status'] = status
        result['exit_code'] = EXIT[status]
        lines.append(f'RESULT: {status} (exit {EXIT[status]})')
        lines.append('No AWS writes executed.')
        return status, result, lines

    identity, err = run('sts', 'get-caller-identity')
    if err:
        result['identity'] = 'UNVERIFIED'
        lines.append(f'Identity: UNVERIFIED ({err})')
        return finish('PERMISSION_DENIED' if err == 'denied' else 'UNVERIFIED')
    arn = (identity or {}).get('Arn', '')
    account = (identity or {}).get('Account', '')
    if not IDENTITY_ARN.match(arn) or ':root' in arn or not re.fullmatch(r'\d{12}', account):
        result['identity'] = 'REFUSED'
        lines.append('Identity: REFUSED — not an Identity Center assumed role.')
        return finish('REFUSED')
    result['identity'] = 'verified Identity Center assumed role (account ID withheld)'
    lines.append('Identity: VERIFIED — Identity Center assumed role; account ID withheld.')

    data, err = run('budgets', 'describe-budgets', '--account-id', account)
    if err:
        result['budgets'] = 'UNVERIFIED'
        lines.append(f'Budgets: UNVERIFIED ({err})')
        return finish('PERMISSION_DENIED' if err == 'denied' else 'UNVERIFIED')
    budgets = {b.get('BudgetName'): b for b in (data or {}).get('Budgets', [])}
    result['budgets_returned'] = len(budgets)
    if sibling:
        sb = budgets.get(sibling)
        result['sibling_budget'] = {'name': sibling, 'present': sb is not None,
                                    'amount': _num(((sb or {}).get('BudgetLimit') or {}).get('Amount')),
                                    'currency': ((sb or {}).get('BudgetLimit') or {}).get('Unit')}
        lines.append(f'Sibling budget {sibling!r}: ' + ('present, untouched.' if sb else 'NOT present.'))
    b = budgets.get(budget_name)
    if b is None:
        lines.append(f'Budget {budget_name!r}: MISSING.')
        return finish('MISSING')

    facts, bad = check_budget(b, budget_name, expected_usd)
    result['budget'] = facts
    lines.append(f"Budget: {facts['type']} {facts['time_unit']} {facts['amount']} {facts['currency']}; "
                 f"recurring={facts['recurring_monthly']}; health={facts['health']}; "
                 f"actual={facts['actual_spend']} forecast={facts['forecast_spend']}")
    warnings: list[str] = []
    if facts['include_credit']:
        warnings.append('IncludeCredit=true: the budget measures spend NET of credits, so alerts will not fire '
                        'while promotional credits absorb charges (Section 20 asks for gross cost)')
    if facts['use_blended'] or facts['use_amortized']:
        warnings.append('cost metric is blended/amortized rather than unblended')

    nd, err = run('budgets', 'describe-notifications-for-budget', '--account-id', account,
                  '--budget-name', budget_name)
    if err:
        result['notifications'] = 'UNVERIFIED'
        lines.append(f'Notifications: UNVERIFIED ({err})')
        if bad:
            result['hard_failures'] = bad
            return finish('MISCONFIGURED')
        return finish('PERMISSION_DENIED' if err == 'denied' else 'UNVERIFIED')
    notifs = (nd or {}).get('Notifications', [])
    subs_all: list[list[dict]] = []
    for n in notifs:
        spec = (f"NotificationType={n.get('NotificationType')},ComparisonOperator={n.get('ComparisonOperator')},"
                f"Threshold={n.get('Threshold')},ThresholdType={n.get('ThresholdType') or 'PERCENTAGE'}")
        sd, serr = run('budgets', 'describe-subscribers-for-notification', '--account-id', account,
                       '--budget-name', budget_name, '--notification', spec)
        if serr:
            result['subscribers'] = f'UNVERIFIED ({serr})'
            warnings.append('subscribers could not be read')
            subs_all.append([{'SubscriptionType': 'UNREADABLE'}])
        else:
            subs_all.append((sd or {}).get('Subscribers', []))
    summary, nwarn = check_notifications(notifs, facts['amount'] or expected_usd, subs_all)
    result['notifications'] = summary
    warnings += nwarn
    lines.append(f'Notifications configured: {len(summary)}')
    for s in summary:
        lines.append(f"  - {s['notification_type']} {s['comparison']} {s['threshold']} {s['threshold_type']}"
                     f" (= ${s['threshold_usd']}) subscribers={s['subscriber_count']} {s['subscriber_kinds']}")
    result['warnings'] = warnings
    for w in warnings:
        lines.append(f'WARNING: {w}')
    if bad:
        result['hard_failures'] = bad
        for x in bad:
            lines.append(f'MISCONFIGURED: {x}')
        return finish('MISCONFIGURED')
    if strict and warnings:
        lines.append('--strict: open warnings are treated as MISCONFIGURED.')
        return finish('MISCONFIGURED')
    return finish('VERIFIED')


def sanitize(result: dict, account_hint: str = '') -> str:
    """Serialise and make sure no account ID, ARN or address pattern slipped in."""
    text = json.dumps(result, indent=2)
    if account_hint:
        text = text.replace(account_hint, '<ACCOUNT>')
    text = re.sub(r'\b\d{12}\b', '<ID>', text)
    text = re.sub(r'arn:aws[a-z-]*:[^"\s]+', '<ARN>', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+', '<EMAIL>', text)
    return text + '\n'


def save(result: dict, project: Path) -> Path:
    out_dir = project.expanduser().resolve() / '.enigma-local'
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    path = out_dir / 'aws-budget-audit-private.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(sanitize(result))
    os.chmod(path, 0o600)
    return path


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--profile', default='enigma-deployer')
    p.add_argument('--region', default=REGION)
    p.add_argument('--project', type=Path, default=PROJECT)
    p.add_argument('--budget-name', default=BUDGET)
    p.add_argument('--expected-usd', type=float, default=TARGET_USD)
    p.add_argument('--sibling-budget', default=SIBLING_BUDGET)
    p.add_argument('--strict', action='store_true', help='treat recommendation warnings as MISCONFIGURED')
    p.add_argument('--no-save', action='store_true')
    args = p.parse_args(argv)
    if args.profile.strip().lower() in FORBIDDEN_PROFILES:
        print('REFUSED: use a named non-root SSO profile, never default/root.')
        return EXIT['REFUSED']
    run = runner or make_runner(args.profile, args.region)
    status, result, lines = audit(run, budget_name=args.budget_name, sibling=args.sibling_budget or None,
                                  expected_usd=args.expected_usd, strict=args.strict)
    result.update({'profile': args.profile, 'region': args.region})
    print('\n'.join(lines))
    print('Credit balance, expiry and Free Tier eligibility: UNVERIFIED — AWS BILLING CONSOLE REQUIRED.')
    if not args.no_save:
        print('Saved sanitized audit:', save(result, args.project))
    return EXIT[status]


if __name__ == '__main__':
    sys.exit(main())
