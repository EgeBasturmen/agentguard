"""Finite declarative evaluator registry; no host plugin or expression evaluation."""
from typing import Literal,Any
from pydantic import BaseModel,ConfigDict,Field,model_validator
from agentguard.core.paths import parse_path

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class OutputJSON(Model):
    json_schema: dict
    @model_validator(mode='after')
    def schema(self):
        from agentguard.integrations.schema import check_schema
        check_schema(self.json_schema);return self
class Text(Model):
    text: str=Field(min_length=1,max_length=8192)
class PathValue(Model):
    path: str
    value: Any
    @model_validator(mode='after')
    def path_valid(self):parse_path(self.path);return self
class PathOnly(Model):
    path: str
    @model_validator(mode='after')
    def path_valid(self):parse_path(self.path);return self
class Arguments(Model):
    tool_name: str=Field(min_length=1,max_length=100)
    arguments: dict
    min_calls: int=Field(default=1,ge=1,le=10000)
class Sequence(Model):
    tools: list[str]=Field(min_length=1,max_length=100)
class Before(Model):
    before: str=Field(min_length=1,max_length=100)
    after: str=Field(min_length=1,max_length=100)
class Sources(Model):
    required_documents: list[str]=Field(min_length=1,max_length=100)
class Files(Model):
    sha256: dict[str,str]=Field(min_length=1,max_length=128)
class CodingCase(Model):
    arguments: list[str]=Field(default_factory=list,max_length=20)
    expected_stdout: str=Field(max_length=8192)
    @model_validator(mode='after')
    def bounded(self):
        if any(len(arg)>1024 or '\x00' in arg for arg in self.arguments):raise ValueError('Invalid coding argument')
        return self
class CodingTests(Model):
    entrypoint: str=Field(min_length=1,max_length=200)
    cases: list[CodingCase]=Field(min_length=1,max_length=20)
    timeout_seconds: int=Field(default=30,ge=1,le=120)
class Label(Model):
    input: str=Field(min_length=1,max_length=2048)
    expected: bool
    source: str=Field(min_length=1,max_length=200)
class Judge(Model):
    rubric: str=Field(min_length=1,max_length=2048)
    rubric_version: str=Field(min_length=1,max_length=100)
    labels: list[Label]=Field(default_factory=list,max_length=20)
    min_confidence: float=Field(default=0.8,ge=0.5,le=1,allow_inf_nan=False)
class Recovery(Model):
    fault_id: str
class TurnOutput(Model):
    turn: int=Field(ge=1,le=20)
    text: str=Field(max_length=8192)

CONFIGS={'output_json':OutputJSON,'output_contains':Text,'output_equals':Text,
    'tool_arguments':Arguments,'tool_sequence':Sequence,'state_equals':PathValue,
    'temporal_never':PathOnly,'temporal_before':Before,'rag_sources':Sources,'rag_grounded':Sources,
    'coding_files':Files,'coding_tests':CodingTests,'judge':Judge,'fault_recovery':Recovery,'turn_output':TurnOutput}

class EvaluationSpec(Model):
    version: Literal['1.0.0']='1.0.0'
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    kind: str
    config: dict
    @model_validator(mode='after')
    def registered(self):
        if self.kind not in CONFIGS:raise ValueError('Unsupported evaluator kind')
        self.config=CONFIGS[self.kind].model_validate(self.config).model_dump(mode='json')
        return self

class Turn(Model):
    task: str=Field(min_length=1,max_length=2048)

class Fault(Model):
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    tool_name: str=Field(min_length=1,max_length=100)
    call_index: int=Field(ge=1,le=10000)
    kind: Literal['failure','timeout']

BUSINESS_KINDS={'state_equals','temporal_never','temporal_before','rag_sources','rag_grounded','coding_files','coding_tests','judge','fault_recovery'}

def requires_business_evidence(scenario):
    return bool(scenario.expected_state or scenario.forbidden_changes or scenario.invariants or any(spec.kind in BUSINESS_KINDS for spec in scenario.evaluations))
