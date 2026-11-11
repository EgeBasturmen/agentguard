"""Approved suite selection and data-minimized product reports (not pytest reports)."""
import json
from html import escape
from pathlib import Path
from typing import Literal
import xml.etree.ElementTree as ET
from pydantic import Field,model_validator
from agentguard.generation.models import Model
from agentguard.generation.repository import Snapshot
from agentguard.core.manifest import digest,canonical_json
from agentguard.regression.repeats import RepeatPolicy


class SuiteProfile(Model):
    name: Literal['pr','scheduled','release']
    case_ids: list[str]|None=Field(default=None,max_length=200)


class ProfileSelection(Model):
    schema_version: int=1
    profile: SuiteProfile
    parent: Snapshot
    snapshot: Snapshot
    policy: RepeatPolicy
    excluded_cases: list[str]
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        full={p['id']:p for p in self.parent.cases};selected={p['id']:p for p in self.snapshot.cases}
        requested=set(self.profile.case_ids) if self.profile.case_ids is not None else set(full)
        if len(self.profile.case_ids or [])!=len(set(self.profile.case_ids or [])):raise ValueError('Duplicate profile selection')
        if not requested or requested-set(full) or set(selected)!=requested or any(full[key]!=pin for key,pin in selected.items()):raise ValueError('Invalid profile pins')
        critical=set(self.policy.critical_cases) if self.policy.critical_cases is not None else set(full)
        if critical-set(full) or critical-requested:raise ValueError('Critical coverage cannot be removed')
        if self.profile.name=='release' and requested!=set(full):raise ValueError('Release profile requires all frozen cases')
        if self.excluded_cases!=sorted(set(full)-requested):raise ValueError('Profile coverage exclusions mismatch')
        if self.snapshot.requirements!=(self.parent.requirements if not self.excluded_cases else []):raise ValueError('Profile requirement scope mismatch')
        value=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if self.fingerprint is not None and value!=self.fingerprint:raise ValueError('Profile selection changed')
        self.fingerprint=value;return self

    def execution_policy(self):
        ids={p['id'] for p in self.snapshot.cases}
        raw=self.policy.model_dump(mode='json')
        raw['quarantines']=[q for q in raw['quarantines'] if q['case_id'] in ids]
        return RepeatPolicy.model_validate(raw)


def select_profile(repository,parent,profile,policy):
    repository.check_snapshot(parent)
    ids={p['id'] for p in parent.cases}
    chosen=set(profile.case_ids) if profile.case_ids is not None else ids
    critical=set(policy.critical_cases) if policy.critical_cases is not None else ids
    if not chosen or chosen-ids or critical-chosen or profile.name=='release' and chosen!=ids:raise ValueError('Profile drops unknown/required release coverage')
    snapshot=parent if chosen==ids else repository.freeze(parent.id[:170]+'.'+profile.name,
        [(p['id'],p['revision']) for p in parent.cases if p['id'] in chosen],requirements=[])
    return ProfileSelection(profile=profile,parent=parent,snapshot=snapshot,policy=policy,excluded_cases=sorted(ids-chosen))


def public_summary(decision):
    raw=decision.model_dump()
    fields=('passed','gate_decision','reasons','coverage_gaps','cases','paired_attempts','assurance','profile','excluded_from_profile','exit_code','outcome','decision_status','authorizes_release')
    summary={key:raw[key] for key in fields if key in raw}
    summary.update(schema_version=1,kind='agentguard.release-summary')
    summary['fingerprint']=digest(summary)
    return summary


def export_reports(decision,prefix):
    from agentguard.regression.gate import GateAPI,GateDecision
    if not isinstance(decision,GateDecision):decision=GateAPI.evaluate(decision)
    summary=public_summary(decision)
    prefix=Path(prefix);paths=[Path(str(prefix)+suffix) for suffix in ('.json','.html','.xml')]
    if any(p.exists() or p.is_symlink() for p in paths):raise FileExistsError('Report output exists')
    prefix.parent.mkdir(parents=True,exist_ok=True)
    text=canonical_json(summary)
    html='<!doctype html><meta charset="utf-8"><title>AgentGuard release</title><h1>'+summary['gate_decision']+'</h1><pre>'+escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'
    suite=ET.Element('testsuite',name='AgentGuard release',tests=str(len(summary.get('cases',[]))+1))
    failures=0;errors=0
    for row in summary.get('cases',[]):
        case=ET.SubElement(suite,'testcase',name=row['case_id'],classname='agentguard.case')
        if row['candidate_pass_rate']<1:
            ET.SubElement(case,'failure',message='Case has unsuccessful attempts');failures+=1
    gate=ET.SubElement(suite,'testcase',name='release-gate',classname='agentguard.gate')
    if not summary['passed']:
        if summary['outcome'] in {'ERROR','INCONCLUSIVE'}:ET.SubElement(gate,'error',message=','.join(summary['reasons']));errors+=1
        else:ET.SubElement(gate,'failure',message=','.join(summary['reasons']));failures+=1
    suite.set('failures',str(failures));suite.set('errors',str(errors));suite.set('skipped','0')
    for path,content in zip(paths,(text,html,ET.tostring(suite,encoding='unicode'))):
        with path.open('x',encoding='utf-8') as stream:stream.write(content)
    return summary
