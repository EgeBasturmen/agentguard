"""Local, bounded JSON Schema validation; schema discovery never fetches remote refs."""
from copy import deepcopy
import hashlib
from typing import ClassVar
from pydantic import BaseModel,ConfigDict,model_validator
from jsonschema import Draft202012Validator
from agentguard.core.manifest import canonical_json


def resolve_schema(schema,document=None,depth=0,seen=()):
    if depth>24: raise ValueError('Schema depth/cycle limit exceeded')
    if isinstance(schema,list): return [resolve_schema(v,document,depth+1,seen) for v in schema]
    if not isinstance(schema,dict): return deepcopy(schema)
    if '$dynamicRef' in schema or 'pattern' in schema or 'patternProperties' in schema:
        raise ValueError('Dynamic refs/regex schema keywords are unsupported')
    if '$ref' in schema:
        reference=schema['$ref']
        if not reference.startswith('#/') or reference in seen or document is None:
            raise ValueError('Only acyclic document-local refs are supported')
        target=document
        for part in reference[2:].split('/'): target=target[part.replace('~1','/').replace('~0','~')]
        merged=resolve_schema(target,document,depth+1,(*seen,reference))
        if len(schema)>1: raise ValueError('Ref siblings are unsupported')
        return merged
    return {k:resolve_schema(v,document,depth+1,seen) for k,v in schema.items()}


def check_schema(schema):
    if len(canonical_json(schema).encode())>65536: raise ValueError('Schema too large')
    schema=resolve_schema(schema,schema)
    Draft202012Validator.check_schema(schema)
    return schema


def validate(schema,value):
    canonical_json(value)
    Draft202012Validator(check_schema(schema)).validate(value)


class SchemaArguments(BaseModel):
    model_config=ConfigDict(extra='allow',strict=True)
    schema_document: ClassVar[dict]={}
    @model_validator(mode='after')
    def check_document(self):
        try: validate(self.schema_document,self.model_dump())
        except Exception as exc: raise ValueError('Arguments do not match the pinned tool schema') from exc
        return self


def argument_model(schema):
    schema=check_schema(schema)
    identity=hashlib.sha256(canonical_json(schema).encode()).hexdigest()[:16]
    return type('ToolArgs_'+identity,(SchemaArguments,),{'schema_document':schema,'__module__':__name__})
