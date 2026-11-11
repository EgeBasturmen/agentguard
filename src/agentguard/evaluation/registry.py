"""Pure evidence checks plus explicit isolated coding and calibrated model judge."""
import hashlib
from copy import deepcopy
from typing import Literal
from pydantic import Field,model_validator
from agentguard.core.manifest import digest
from agentguard.core.paths import parse_path,resolve_path,values_equal
from agentguard.evaluation.specs import Model,EvaluationSpec

class MissingEvidence(ValueError):
    def __init__(self,message,details=None):
        super().__init__(message);self.details=details or {}

class EvaluationFinding(Model):
    id: str
    kind: str
    status: Literal['pass','fail','error','inconclusive']
    details: dict=Field(default_factory=dict)
    spec_digest: str
    evidence_digest: str
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        expected=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if self.fingerprint is not None and self.fingerprint!=expected:raise ValueError('Evaluation finding changed')
        self.fingerprint=expected;return self

def _audit(context):
    if not context.get('authoritative') or not context.get('audit'):raise MissingEvidence('Authoritative tool observations are absent')
    events=context['audit']
    if [e.sequence for e in events]!=list(range(1,len(events)+1)):raise MissingEvidence('Invalid audit order')
    return events

def _output(context):
    if not isinstance(context.get('output'),str):raise MissingEvidence('Agent output was not captured')
    return context['output']

def evidence_payload(context):
    """Serializable controller observation, excluding credentials, callbacks and models."""
    return deepcopy(dict(before=context.get('before'),after=context.get('after'),output=context.get('output'),
        audit=[event.model_dump(mode='json') if hasattr(event,'model_dump') else event for event in context.get('audit',[])],
        turn_outputs=context.get('turn_outputs',[])))

def _rag(config,context,grounded):
    from agentguard.integrations.http import _json
    data=_json(_output(context));documents=context.get('before',{}).get('documents')
    if not isinstance(documents,dict):raise MissingEvidence('Versioned document fixture is absent')
    events=_audit(context)
    retrieved={e.arguments.get('document_id') for e in events if e.status=='completed' and e.tool_name=='document.get'}
    citations=data.get('citations')
    if not isinstance(citations,list) or not citations:return False,{'reason':'Citations are required'}
    seen=set()
    for citation in citations:
        if not isinstance(citation,dict) or set(citation)!={'document_id','quote'}:return False,{'reason':'Invalid citation'}
        identity=citation['document_id'];quote=citation['quote']
        if identity not in retrieved or identity not in documents or not isinstance(quote,str) or not quote or quote not in documents[identity]['content']:return False,{'reason':'Unretrieved or unsupported citation'}
        seen.add(identity)
    okay=set(config['required_documents']).issubset(seen)
    if grounded:
        # Deliberately extractive: no semantic entailment is invented from lexical overlap.
        okay=okay and data.get('answer')==' '.join(citation['quote'] for citation in citations)
    return okay,{'retrieved_documents':sorted(retrieved),'cited_documents':sorted(seen),'assurance':'exact-extractive' if grounded else 'retrieval-and-quote'}

def evaluate(spec,context):
    spec=EvaluationSpec.model_validate(spec.model_dump(mode='json'))
    kind=spec.kind;c=spec.config;details={}
    try:binding=digest(evidence_payload(context))
    except (ValueError,TypeError):return EvaluationFinding(id=spec.id,kind=kind,status='error',details={'reason':'Invalid observation payload'},spec_digest=digest(spec.model_dump(mode='json')),evidence_digest=digest(None))
    try:
        if kind=='output_json':
            output=_output(context)
            from agentguard.integrations.http import _json
            from agentguard.integrations.schema import validate
            from jsonschema.exceptions import ValidationError
            try:validate(c['json_schema'],_json(output));passed=True
            except (ValueError,ValidationError):passed=False
        elif kind=='output_contains':passed=c['text'] in _output(context)
        elif kind=='output_equals':passed=c['text']==_output(context)
        elif kind=='state_equals':
            if 'after' not in context:raise MissingEvidence('Independent final state is absent')
            passed=values_equal(resolve_path(context['after'],parse_path(c['path'])),c['value'])
        elif kind=='tool_arguments':
            passed=sum(e.status=='completed' and e.tool_name==c['tool_name'] and values_equal(e.arguments,c['arguments']) for e in _audit(context))>=c['min_calls']
        elif kind=='tool_sequence':passed=[e.tool_name for e in _audit(context) if e.status=='completed']==c['tools']
        elif kind=='temporal_before':
            events=_audit(context);approved=False;observed=False;passed=True
            for event in events:
                if event.status!='completed':continue
                if event.tool_name==c['after']:observed=True;passed=passed and approved
                if event.tool_name==c['before']:approved=True
            passed=passed and observed
        elif kind=='temporal_never':
            target=parse_path(c['path']);passed=True
            for event in _audit(context):
                if event.status!='completed':continue
                for mutation in event.mutations:
                    path=parse_path(mutation.path)
                    if path[:len(target)]==target or target[:len(path)]==path:passed=False
        elif kind in {'rag_sources','rag_grounded'}:passed,details=_rag(c,context,kind=='rag_grounded')
        elif kind=='coding_files':
            files=context.get('after',{}).get('files')
            if not isinstance(files,dict):raise MissingEvidence('Independent filesystem snapshot is absent')
            passed=all(isinstance(files.get(path),str) and hashlib.sha256(files[path].encode()).hexdigest()==value for path,value in c['sha256'].items())
        elif kind=='coding_tests':
            if not context.get('authoritative'):raise MissingEvidence('Coding evaluator requires a controlled filesystem observation')
            from agentguard.evaluation.coding import run_tests
            passed,details=run_tests(c,context.get('after',{}).get('files'),context.get('cancel_event'),context.get('deadline'))
        elif kind=='judge':
            from agentguard.evaluation.judge import assess
            passed,details=assess(c,_output(context),context.get('gateway'),context.get('deadline'))
        elif kind=='fault_recovery':
            events=_audit(context);faults=[e for e in events if e.fault_id==c['fault_id'] and e.status=='error' and e.effect_status=='none']
            passed=len(faults)==1 and any(e.sequence>faults[0].sequence and e.tool_name==faults[0].tool_name and e.status=='completed' for e in events)
        elif kind=='turn_output':
            outputs=context.get('turn_outputs')
            if not outputs or len(outputs)<c['turn']:raise MissingEvidence('Requested session turn was not observed')
            passed=outputs[c['turn']-1]==c['text']
        else:raise MissingEvidence('Unsupported evaluator')
        status='pass' if passed else 'fail'
    except MissingEvidence as exc:status='inconclusive';details={'reason':str(exc),**exc.details}
    except Exception:status='error';details={'reason':'Evaluator evidence or execution failed'}
    return EvaluationFinding(id=spec.id,kind=kind,status=status,details=details,spec_digest=digest(spec.model_dump(mode='json')),evidence_digest=binding)
