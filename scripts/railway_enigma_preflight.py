#!/usr/bin/env python3
"""Enigma local-only Railway deployment preflight. Python 3.13, stdlib only.
Does not contact Railway or AWS, deploy, read secret file contents, or access GPUs.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_REMOTE = 'github.com/stokastisk-ai/enigma'
REQUIRED = ['apps/web/Dockerfile','apps/api/Dockerfile','apps/web/package.json','apps/web/package-lock.json','apps/api/requirements-railway.txt','packages/contracts','skills.md','CLAUDE.md']

def git(root:Path,*args:str)->tuple[int,str]:
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=25)
    return p.returncode,p.stdout.strip()

def check(root:Path)->dict:
    info={'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'project':str(root),'read_only':True,'checks':{},'warnings':[]}
    for rel in REQUIRED:
        info['checks'][rel] = 'PRESENT' if (root/rel).exists() else 'MISSING'
    rc,origin=git(root,'remote','get-url','origin')
    info['checks']['git_origin_matches_expected']=bool(rc==0 and EXPECTED_REMOTE in origin)
    _,head=git(root,'rev-parse','--short','HEAD')
    info['checks']['local_HEAD']=head or 'UNKNOWN'
    _,branch=git(root,'branch','--show-current')
    info['checks']['branch']=branch or 'UNKNOWN'
    _,status=git(root,'status','--porcelain=v1','--untracked-files=normal')
    info['checks']['git_dirty']=bool(status)
    info['checks']['git_change_count']=len(status.splitlines())
    for svc in ('web','api'):
        p=root/f'apps/{svc}/Dockerfile'
        if p.exists():
            txt=p.read_text(errors='replace')
            info['checks'][f'{svc}_dockerfile_looks_root_context']= bool('COPY ' in txt)
            info['checks'][f'{svc}_dockerfile_has_expose']= 'EXPOSE ' in txt
            info['checks'][f'{svc}_dockerfile_has_start']= 'CMD ' in txt or 'ENTRYPOINT ' in txt
    rc,tracked=git(root,'ls-files','--','apps/web/Dockerfile','apps/api/Dockerfile')
    info['checks']['dockerfiles_tracked_locally']=len(tracked.splitlines()) if rc==0 and tracked else 0
    rc,ignored=git(root,'check-ignore','-q','.env.local')
    # Do not assume this command tests nested env files
    info['checks']['gitignore_review_required']=True
    if info['checks']['dockerfiles_tracked_locally']<2:
        info['warnings'].append('Railway GitHub builds cannot see untracked Dockerfiles; local CLI upload may include them, subject to .gitignore and .dockerignore.')
    if info['checks']['git_dirty']:
        info['warnings'].append('Working tree contains unpublished changes. Railway GitHub build may use an older revision.')
    info['warnings'].append('Review the dashboard pending changes and Docker build context; this script makes no cloud calls.')
    return info

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project',type=Path,required=True)
    ap.add_argument('--out',type=Path,help='Optional private JSON report (outside Git recommended)')
    args=ap.parse_args()
    root=args.project.resolve()
    if not (root/'.git').exists():
        ap.error('Project must contain .git')
    report=check(root)
    data=json.dumps(report,indent=2)
    print(data)
    if args.out:
        out=args.out.expanduser().resolve()
        if not str(out).startswith(str(root/ '.enigma-local')+os.sep):
            ap.error('--out must be inside the project .enigma-local/ directory')
        out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        out.write_text(data+'\n');out.chmod(0o600)
    return 0 if all(v=='PRESENT' for k,v in report['checks'].items() if k in REQUIRED) and report['checks']['git_origin_matches_expected'] else 2
if __name__=='__main__':sys.exit(main())
