"""Bounded data-only imports/exports. Dataset files never transport release approval."""
import csv
import io
import json
import os
from pathlib import Path
import yaml
from agentguard.integrations.http import _json
from agentguard.generation.models import Model,Candidate,GenerationBatch,GenerationRequest,RuleInput
from pydantic import Field

MAX_DATASET_BYTES=8*1024*1024

class Dataset(Model):
    schema_version: int=1
    kind: str='agentguard.proposals'
    cases: list[Candidate]=Field(max_length=200)
    requirements: list[dict]=Field(default_factory=list)
    gaps: list[str]=Field(default_factory=list)

class StrictLoader(yaml.SafeLoader):
    def compose_node(self,parent,index):
        if self.check_event(yaml.AliasEvent):raise ValueError('YAML aliases are unsupported')
        return super().compose_node(parent,index)
    def construct_mapping(self,node,deep=False):
        result={}
        for key,value in node.value:
            key=self.construct_object(key,deep=deep)
            if not isinstance(key,str) or key in result:raise ValueError('Duplicate/non-text YAML key')
            result[key]=self.construct_object(value,deep=deep)
        return result
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,StrictLoader.construct_mapping)


def read_document(path):
    path=Path(path)
    with path.open('rb') as stream:data=stream.read(MAX_DATASET_BYTES+1)
    if len(data)>MAX_DATASET_BYTES:raise ValueError('Input exceeds 8 MiB')
    if path.suffix.lower() in {'.yaml','.yml'}:
        result=yaml.load(data.decode('utf-8'),Loader=StrictLoader)
        # Normalize through finite/unique JSON serialization without executable object constructors.
        from agentguard.core.manifest import canonical_json
        return _json(canonical_json(result))
    return _json(data)


def read_request(path):return GenerationRequest.model_validate(read_document(path))


def export_dataset(batch,path):
    if isinstance(batch,GenerationBatch):batch=Dataset(cases=batch.cases,requirements=batch.requirements,gaps=batch.gaps)
    batch=Dataset.model_validate(batch.model_dump(mode='json'))
    if batch.kind!='agentguard.proposals':raise ValueError('Unsupported dataset kind')
    target=Path(path);suffix=target.suffix.lower()
    if suffix=='.csv':
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=['record_type','id','revision','payload']);writer.writeheader()
        writer.writerow(dict(record_type='metadata',id='dataset',revision=1,payload=json.dumps(dict(requirements=batch.requirements,gaps=batch.gaps),ensure_ascii=False)))
        for case in batch.cases:writer.writerow(dict(record_type='candidate',id=case.id,revision=case.revision,payload=case.model_dump_json()))
        text=stream.getvalue()
    elif suffix in {'.yaml','.yml'}:text=yaml.safe_dump(batch.model_dump(mode='json'),allow_unicode=True,sort_keys=False)
    elif suffix=='.json':text=batch.model_dump_json(indent=2)
    else:raise ValueError('Use CSV, JSON or YAML dataset files')
    data=text.encode('utf-8')
    if len(data)>MAX_DATASET_BYTES:raise ValueError('Export exceeds 8 MiB')
    target.parent.mkdir(parents=True,exist_ok=True)
    # Never replace user data or follow an existing output symlink.
    with target.open('xb') as stream:stream.write(data)
    return target


def import_dataset(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open('r',encoding='utf-8',newline='') as stream:
            text=stream.read(MAX_DATASET_BYTES+1)
        if len(text.encode())>MAX_DATASET_BYTES:raise ValueError('CSV exceeds limit')
        # csv's platform default (128 KiB) otherwise rejects our own valid exports.
        # The complete input is already bounded above; never reduce a caller's limit.
        csv.field_size_limit(max(csv.field_size_limit(),MAX_DATASET_BYTES))
        reader=csv.DictReader(io.StringIO(text))
        if reader.fieldnames!=['record_type','id','revision','payload']:raise ValueError('Unsupported dataset CSV columns')
        cases=[];metadata=None
        for row in reader:
            if None in row or None in row.values():raise ValueError('Malformed CSV row')
            payload=_json(row['payload'])
            if row['record_type']=='metadata':
                if metadata is not None:raise ValueError('Duplicate dataset metadata')
                metadata=payload
            elif row['record_type']=='candidate':
                case=Candidate.model_validate(payload)
                if row['id']!=case.id or row['revision']!=str(case.revision):raise ValueError('CSV envelope/candidate mismatch')
                cases.append(case)
            else:raise ValueError('Unknown dataset CSV record')
            if len(cases)>200:raise ValueError('Too many dataset cases')
        if metadata is None:raise ValueError('Dataset metadata missing')
        data=dict(schema_version=1,kind='agentguard.proposals',cases=[case.model_dump() for case in cases],**metadata)
    else:data=read_document(path)
    dataset=Dataset.model_validate(data)
    if dataset.kind!='agentguard.proposals':raise ValueError('Unsupported dataset kind')
    if len({(case.id,case.revision) for case in dataset.cases})!=len(dataset.cases):raise ValueError('Duplicate dataset revisions')
    return dataset


def import_rules(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open('r',encoding='utf-8',newline='') as stream:text=stream.read(1024*1024+1)
        if len(text.encode())>1024*1024:raise ValueError('Rule import exceeds limit')
        reader=csv.DictReader(io.StringIO(text))
        if set(reader.fieldnames or [])-{'id','version','text','uri'} or not {'id','version','text'}.issubset(reader.fieldnames or []):raise ValueError('Invalid rule CSV headers')
        rows=list(reader)
    else:
        data=read_document(path);rows=data['rules'] if isinstance(data,dict) else data
    if not isinstance(rows,list) or len(rows)>100:raise ValueError('Rule import expects at most 100 records')
    return [RuleInput.model_validate(row) for row in rows]


def incident_from_report(path,identity,version='1'):
    from agentguard.reporting.models import load_report
    report=load_report(path)
    result=report.result
    if result.comparison_manifest is None:raise ValueError('Incident report lacks task/fixture manifest')
    original=result.comparison_manifest.scenario
    return dict(id=identity,version=version,task=original.task,initial_state=original.initial_state,
        observed_output=result.worker_output,observed_status=result.status,
        assertions=[a.model_dump() for a in result.assertions],mutations=[m.model_dump() for m in result.mutations],audit=[event.model_dump() for event in result.audit])


def load_execution(path):
    from agentguard.generation.execution import SuiteExecution
    payload=read_document(path)
    claimed_pass=payload.pop('passed',None);claimed_gate=payload.pop('gate_decision',None)
    result=SuiteExecution.model_validate_json(json.dumps(payload,allow_nan=False))
    if type(claimed_pass) is not bool or claimed_pass!=result.passed or claimed_gate!=result.gate_decision:raise ValueError('Stored suite gate contradicts its evaluated members')
    return result


def write_execution(result,path):
    target=Path(path)
    if target.suffix.lower()!='.json':raise ValueError('Suite evidence uses typed JSON')
    data=result.model_dump_json(indent=2).encode()
    if len(data)>MAX_DATASET_BYTES:raise ValueError('Suite report exceeds 8 MiB')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(data)
    return target
