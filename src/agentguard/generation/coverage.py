"""Coverage separates proposals, approvals, observations and passing evaluated evidence."""
from agentguard.core.outcome import effective_pass
from agentguard.core.paths import values_equal


def coverage(cases,requirements,*,repository=None,execution=None):
    unique={}
    for case in cases:
        case=case.checked();key=(case.id,case.revision)
        if key in unique and unique[key].fingerprint!=case.fingerprint:raise ValueError('Conflicting duplicate case in coverage input')
        unique[key]=case
    results={(row.id,row.revision):row for row in execution.cases} if execution else {}
    if len(requirements)>500 or len({row.get("id") for row in requirements})!=len(requirements):raise ValueError("Duplicate/too many coverage requirements")
    rows=[]
    for requirement in requirements:
        if set(requirement)!={'id','axes'} or not isinstance(requirement['id'],str) or not requirement['id'] or not isinstance(requirement['axes'],list) or not requirement['axes'] or len(requirement['axes'])!=len(set(requirement['axes'])) or any(axis not in {'benign','adversarial','edge'} for axis in requirement['axes']):
            raise ValueError('Invalid coverage requirement')
        identity=requirement['id']
        for axis in requirement['axes']:
            row=dict(requirement=identity,axis=axis,proposed=0,approved=0,executed=0,passed=0)
            for key,case in unique.items():
                contract_opportunity=identity.startswith('contract:') and identity[len('contract:'):] in case.runtime.policy['allowed_tools'] and any(source.kind=='contract' and source.content['contract']['name']==identity[len('contract:'):] for source in case.sources)
                if case.axis!=axis or (identity not in case.requirements and not contract_opportunity):continue
                row['proposed']+=1
                current=repository is None or repository.is_approved(case)
                if repository is not None and current:row['approved']+=1
                actual=results.get(key)
                if actual is None or actual.result is None:continue
                result=actual.result
                reference=result.generation_ref or {}
                if reference.get('candidate_fingerprint')!=case.fingerprint:continue
                # A claimed tag is not evidence. Rule assertions and contract arguments must be observed.
                observed=False
                if identity.startswith('rule:'):
                    required={cid for cid,refs in case.oracle_sources.items() if identity in refs}
                    assertions={a.assertion_id:a for a in result.assertions}
                    observed=bool(required) and required.issubset(assertions)
                elif identity.startswith('contract:'):
                    name=identity[len('contract:'):]
                    observed=any(item.tool_name==name and item.status=='completed' and
                        (case.observations.get('tool')!=name or values_equal(item.arguments,case.observations.get('arguments'))) for item in result.audit)
                elif identity=='risk:schema_boundary':
                    observed=bool(case.observations.get('boundary')) and any(item.tool_name==case.observations.get('tool') and item.status=='completed' and values_equal(item.arguments,case.observations.get('arguments')) for item in result.audit)
                elif identity=='risk:instruction_override':
                    required=set(case.oracle_sources)
                    observed=case.observations.get('risk_pack')=='instruction_override' and bool(required) and required.issubset({a.assertion_id for a in result.assertions})
                if observed:
                    row['executed']+=1
                    if current and actual.admission=='approved' and effective_pass(result):row['passed']+=1
            rows.append(row)
    return dict(schema_version=1,rows=rows,
        missing_executed=[row['requirement']+':'+row['axis'] for row in rows if row['passed']==0],
        note='Proposed/approved cases are not executed coverage; passing evidence covers only this declared finite matrix.')
