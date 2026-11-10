"""Pinned discovery and explicit routes. Discovery grants no permissions or effect trust."""
from copy import deepcopy
from dataclasses import dataclass,field,replace
import hashlib
import re
from urllib.parse import quote
from pydantic import BaseModel,ConfigDict,Field
from agentguard.core.manifest import canonical_json
from agentguard.integrations.http import Endpoint,request_json,validate_path
from agentguard.integrations.schema import check_schema,resolve_schema,validate,argument_model

class ToolContract(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name: str=Field(min_length=1,max_length=100)
    version: str=Field(pattern=r'^\d+\.\d+\.\d+$')
    adapter: str
    input_schema: dict
    output_schema: dict|None=None
    effect_type: str='unknown'

    def fingerprint(self):
        check_schema(self.input_schema)
        if self.output_schema is not None: check_schema(self.output_schema)
        return hashlib.sha256(canonical_json(self.model_dump()).encode()).hexdigest()

@dataclass(frozen=True)
class ReplayEntry:
    key: str
    result: object
    after_state: dict
    approved: bool=False

    def __post_init__(self):
        if type(self.approved) is not bool or not re.fullmatch(r'[0-9a-f]{64}',self.key):
            raise ValueError('Replay entries require a hash key and strict boolean approval')
        if not isinstance(self.after_state,dict): raise ValueError('Replay after_state must be an object')
        if len(canonical_json(dict(result=self.result,state=self.after_state)).encode())>1024*1024:
            raise ValueError('Replay entry too large')

    @classmethod
    def capture(cls,contract,arguments,before_state,result,after_state,*,context=None,approved=False):
        return cls(replay_key(contract,arguments,before_state,context),deepcopy(result),deepcopy(after_state),approved)


def replay_key(contract,arguments,state,context=None):
    return hashlib.sha256(canonical_json(dict(contract=contract.fingerprint(),arguments=arguments,state=state,context=context or {})).encode()).hexdigest()

@dataclass
class RoutedTool:
    contract: ToolContract
    mode: str='simulator'
    target: object=None
    observer: object=None
    handler: object=None
    mock_result: object=None
    replay_entries: tuple=()
    context: dict=field(default_factory=dict)
    approved_effect: bool=False
    fallback: str='error'

    def __post_init__(self):
        if type(self.approved_effect) is not bool: raise ValueError('Effect approval must be a strict boolean')
        if self.mode not in {'simulator','staging','replay','mock'} or self.fallback!='error':
            raise ValueError('Only explicit supported modes and fallback=error are supported')
        self.contract=ToolContract.model_validate(self.contract.model_dump())
        self.contract.fingerprint()
        keys=[entry.key for entry in self.replay_entries]
        if len(keys)!=len(set(keys)): raise ValueError('Ambiguous replay entries')

    def __call__(self,state,**arguments):
        validate(self.contract.input_schema,arguments)
        if self.mode=='replay':
            key=replay_key(self.contract,arguments,state,self.context)
            entry=next((item for item in self.replay_entries if item.key==key and item.approved),None)
            if entry is None: raise LookupError('Replay miss; live fallback is disabled')
            result=deepcopy(entry.result)
            proposed=deepcopy(entry.after_state)
        elif self.mode=='mock':
            result=deepcopy(self.mock_result)
            proposed=deepcopy(state)
        elif self.mode=='staging':
            if not self.approved_effect or self.contract.effect_type not in {'read','write','mixed'}:
                raise PermissionError('Staging effects require explicit approval')
            if self.target is None or self.observer is None: raise ValueError('Staging requires target and independent observer')
            result=self.target.invoke(self.contract.name,arguments)
            proposed=self.observer.snapshot()
        elif self.target is not None:
            result=self.target.invoke(self.contract.name,arguments)
            proposed=self.target.snapshot()
        elif self.handler is not None:
            result=self.handler(state,**arguments)
            proposed=deepcopy(state)
        else: raise ValueError('Simulator requires a real handler/environment')
        if self.contract.output_schema is not None: validate(self.contract.output_schema,result)
        canonical_json(result);canonical_json(proposed)
        state.clear();state.update(proposed)
        return result

    def register(self,registry):
        registry.register(self.contract.name,argument_model(self.contract.input_schema),self,stateful=True)

    def identity(self):
        return dict(contract=self.contract.model_dump(),mode=self.mode,fallback=self.fallback,
            target=self.target.identity() if self.target is not None and hasattr(self.target,'identity') else None,
            approved_effect=self.approved_effect,context=self.context,
            replay=[dict(key=e.key,result=e.result,after_state=e.after_state,approved=e.approved) for e in self.replay_entries],
            mock_result=self.mock_result if self.mode=='mock' else None)

@dataclass(frozen=True)
class OpenAPIOperation:
    endpoint: Endpoint
    method: str
    path: str
    parameters: tuple
    has_body: bool
    responses: dict
    name: str

    def invoke(self,name,arguments):
        if name!=self.name: raise PermissionError('Operation identity mismatch')
        path=self.path;query={}
        for parameter in self.parameters:
            key=parameter['name']
            if key not in arguments: continue
            value=arguments[key]
            if parameter['in']=='path':
                if not isinstance(value,(str,int)) or str(value) in {'.','..'} or any(c in str(value) for c in '/\\%?#'):
                    raise PermissionError('Unsafe path resource identifier')
                path=path.replace('{'+key+'}',quote(str(value),safe=''))
            else: query[key]=value
        validate_path(path)
        if '{' in path or '}' in path: raise ValueError('Missing path parameter')
        result,_,status=request_json(self.endpoint,self.method,path,arguments.get('body') if self.has_body else None,query=query)
        schema=self.responses.get(str(status))
        if schema is None: raise ValueError('HTTP response status/schema was not declared')
        validate(schema,result)
        return result

@dataclass
class OpenAPIConnector:
    endpoint: Endpoint
    document: dict|None=None
    operations: dict=field(default_factory=dict)
    version: str='3.1.1'

    def discover(self,document=None):
        document=deepcopy(document if document is not None else self.document)
        if not isinstance(document,dict) or document.get('openapi')!=self.version:
            raise ValueError('Only pinned OpenAPI 3.1.1 is supported')
        version=document.get('info',{}).get('version','')
        if not re.fullmatch(r'\d+\.\d+\.\d+',version): raise ValueError('OpenAPI service version must be pinned semver')
        self.operations={};contracts=[]
        for path,item in document.get('paths',{}).items():
            validate_path(path)
            if '$ref' in item: raise ValueError('Referenced path items are unsupported')
            for method,operation in item.items():
                if method.upper() not in {'GET','POST','PUT','PATCH','DELETE'}: continue
                name=operation.get('operationId')
                if not isinstance(name,str) or not name or name in self.operations: raise ValueError('Unique operationId required')
                if operation.get('security') or document.get('security'): raise ValueError('Credentialed OpenAPI operations are unsupported; use a dedicated staging gateway')
                parameters=[*item.get('parameters',[]),*operation.get('parameters',[])]
                properties={};required=[];normalized=[]
                for parameter in parameters:
                    if '$ref' in parameter: parameter=resolve_schema(parameter,document)
                    if parameter.get('in') not in {'path','query'} or parameter.get('style') not in {None,'simple','form'} or parameter.get('explode'):
                        raise ValueError('Only scalar path/query parameters are supported')
                    key=parameter['name'];schema=resolve_schema(parameter['schema'],document)
                    if key in properties or key=='body' or schema.get('type') not in {'string','integer','number','boolean'}:
                        raise ValueError('Ambiguous/non-scalar parameter')
                    normalized.append(parameter)
                    properties[key]=schema
                    if parameter.get('required') or parameter['in']=='path': required.append(key)
                request=operation.get('requestBody');has_body=request is not None
                if has_body:
                    if '$ref' in request: request=resolve_schema(request,document)
                    content=request.get('content',{})
                    if set(content)!={'application/json'}: raise ValueError('Only JSON request bodies are supported')
                    properties['body']=resolve_schema(content['application/json']['schema'],document)
                    if request.get('required'): required.append('body')
                inputs=dict(type='object',properties=properties,required=required,additionalProperties=False)
                responses={}
                for code,response in operation.get('responses',{}).items():
                    if not code.isdigit() or not 200<=int(code)<300: continue
                    content=response.get('content',{})
                    if 'application/json' not in content: raise ValueError('Successful operations require declared JSON response schemas')
                    responses[code]=resolve_schema(content['application/json']['schema'],document)
                if not responses: raise ValueError('No supported successful response schema')
                contract=ToolContract(name=name,version=version,adapter='openapi@3.1.1',input_schema=inputs,
                    output_schema=next(iter(responses.values())) if len(responses)==1 else None)
                contract.fingerprint()
                self.operations[name]=OpenAPIOperation(self.endpoint,method.upper(),path,tuple(normalized),has_body,responses,name)
                contracts.append(contract)
                if len(contracts)>100: raise ValueError('Too many discovered operations')
        if not contracts: raise ValueError('No supported OpenAPI operations')
        return contracts

    def invoke(self,name,arguments):
        if name not in self.operations: raise PermissionError('Operation was not discovered')
        return self.operations[name].invoke(name,arguments)

    def identity(self):
        return dict(adapter='openapi@'+self.version,endpoint=self.endpoint.identity(),operations={name:dict(method=op.method,path=op.path,parameters=list(op.parameters),has_body=op.has_body,responses=op.responses) for name,op in sorted(self.operations.items())})

    def bound(self,endpoint):
        result=OpenAPIConnector(endpoint,self.document,version=self.version)
        # Definitions are retained, only the operator-approved namespace endpoint changes.
        result.operations={name:replace(operation,endpoint=endpoint) for name,operation in self.operations.items()}
        return result

@dataclass
class MCPConnector:
    endpoint: Endpoint
    server_version: str
    protocol_version: str='2025-11-25'
    contracts: dict=field(default_factory=dict)

    def _exchange(self,method,params,request_id,session=None):
        headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':self.protocol_version}
        if session: headers['Mcp-Session-Id']=session
        body=dict(jsonrpc='2.0',method=method,params=params)
        if request_id is not None: body['id']=request_id
        value,returned,status=request_json(self.endpoint,'POST','/',body,headers=headers)
        if request_id is None:
            if status!=202 or value is not None: raise ValueError('Invalid MCP notification acknowledgement')
            return None,returned
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0' or type(value.get('id')) is not int or value['id']!=request_id or 'error' in value or 'result' not in value:
            raise ValueError('Invalid MCP JSON-RPC response')
        return value['result'],returned

    def _open(self):
        if self.protocol_version!='2025-11-25': raise ValueError('Unsupported MCP protocol version')
        result,headers=self._exchange('initialize',dict(protocolVersion=self.protocol_version,capabilities={},clientInfo=dict(name='AgentGuard',version='1.0.0')),1)
        session=headers.get('mcp-session-id')
        try:
            if result.get('protocolVersion')!=self.protocol_version or result.get('serverInfo',{}).get('version')!=self.server_version or 'tools' not in result.get('capabilities',{}):
                raise ValueError('MCP pinned version/capability mismatch')
            self._exchange('notifications/initialized',{},None,session)
            return session
        except Exception:
            self._close(session);raise

    def _close(self,session):
        if session:
            try:
                request_json(self.endpoint,'DELETE','/',headers={'Mcp-Session-Id':session,'MCP-Protocol-Version':self.protocol_version},accepted=(200,204))
            except Exception as exc:
                from agentguard.execution.errors import ExecutionCleanupError
                raise ExecutionCleanupError('MCP session cleanup could not be verified') from exc

    def discover(self):
        session=self._open()
        try:
            contracts={};cursor=None;seen=set()
            for index in range(20):
                result,_=self._exchange('tools/list',{'cursor':cursor} if cursor else {},index+2,session)
                for tool in result.get('tools',[]):
                    name=tool['name']
                    if name in contracts or len(contracts)>=100: raise ValueError('Duplicate/too many MCP tools')
                    contract=ToolContract(name=name,version=self.server_version,adapter='mcp@2025-11-25',input_schema=tool['inputSchema'],output_schema=tool.get('outputSchema'))
                    contract.fingerprint();contracts[name]=contract
                cursor=result.get('nextCursor')
                if not cursor: break
                if cursor in seen: raise ValueError('MCP pagination cycle')
                seen.add(cursor)
            else: raise ValueError('MCP pagination limit')
            if not contracts: raise ValueError('No MCP tools discovered')
            self.contracts=contracts
            return list(contracts.values())
        finally: self._close(session)

    def invoke(self,name,arguments):
        if name not in self.contracts: raise PermissionError('MCP tool was not discovered')
        validate(self.contracts[name].input_schema,arguments)
        session=self._open()
        try:
            result,_=self._exchange('tools/call',dict(name=name,arguments=arguments),2,session)
            if result.get('isError'): raise RuntimeError('MCP tool returned isError')
            output=result.get('structuredContent',result.get('content'))
            if self.contracts[name].output_schema is not None: validate(self.contracts[name].output_schema,output)
            return output
        finally: self._close(session)

    def identity(self):
        return dict(adapter='mcp@'+self.protocol_version,server_version=self.server_version,endpoint=self.endpoint.identity(),contracts={name:row.fingerprint() for name,row in sorted(self.contracts.items())})

    def bound(self,endpoint): return replace(self,endpoint=endpoint)
