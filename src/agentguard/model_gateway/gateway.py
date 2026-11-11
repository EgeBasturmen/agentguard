"""Provider-independent text gateway. Credentials and reservations stay on the host.

No retries, tools, search, streaming or implicit provider/model fallback. Currency
is an estimate from configured versioned rates, never a verified provider invoice.
"""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
import math
import os
import re
import threading
import time
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from agentguard.core.manifest import canonical_json, digest
from agentguard.integrations.plan_config import EndpointConfig
from agentguard.integrations.http import request_json


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class GatewayConfig(Model):
    provider: str=Field(pattern=r'^[a-z][a-z0-9_-]{0,49}$')
    model: str=Field(pattern=r'^[A-Za-z0-9._-]{1,100}$')
    mode: Literal['live','mock']='live'
    endpoint: EndpointConfig|None=None
    credential_env: str|None=None
    system_prompt: str=Field(default='',max_length=2048)
    prompt_version: str=Field(default='1',min_length=1,max_length=100)
    response_format: Literal['text','json']='text'
    max_calls: int=Field(default=8,ge=1,le=100)
    max_input_chars: int=Field(default=8192,ge=1,le=32768)
    max_output_tokens: int=Field(default=512,ge=1,le=2048)
    max_total_tokens: int=Field(default=32768,ge=1,le=1000000)
    max_cost_micro_usd: int=Field(default=100000,ge=0,le=100000000)
    input_rate_micro_usd_per_million: int=Field(default=1000000,ge=0,le=1000000000)
    output_rate_micro_usd_per_million: int=Field(default=10000000,ge=0,le=1000000000)
    pricing_version: str=Field(default='operator-conservative-v1',min_length=1,max_length=100)
    min_call_interval_seconds: float=Field(default=0.0,ge=0,le=60,allow_inf_nan=False)
    timeout_seconds: float=Field(default=15.0,gt=0,le=60,allow_inf_nan=False)
    mock_output: str=Field(default='mock response',max_length=8192)

    @model_validator(mode='after')
    def check_provider(self):
        if self.credential_env is not None and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}',self.credential_env):raise ValueError('Use an environment variable reference, never an inline credential')
        if (self.provider=='mock')!=(self.mode=='mock'):raise ValueError('Mock provider must be explicitly labelled mock')
        if self.provider=='gemini':
            if self.endpoint is not None:raise ValueError('Gemini uses a fixed HTTPS endpoint; no credential forwarding to custom origins')
            if self.credential_env not in {None,'GEMINI_API_KEY'}:raise ValueError('Gemini reads only GEMINI_API_KEY')
        if self.provider=='json-http' and (self.endpoint is None or self.credential_env is not None):raise ValueError('JSON HTTP bridge requires an explicit endpoint and no credential forwarding')
        if self.provider=='mock' and (self.endpoint is not None or self.credential_env is not None):raise ValueError('Mock is offline')
        return self


class Message(Model):
    role: Literal['user','assistant']
    content: str=Field(min_length=1,max_length=8192)


class ModelArguments(Model):
    messages: list[Message]=Field(min_length=1,max_length=20)
    max_output_tokens: int=Field(default=128,ge=1,le=2048)


@dataclass(frozen=True)
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    model_version: str


class ModelProvider(Protocol):
    def generate(self,config,messages,max_output_tokens,*,secret,cancel_event): ...

class ProviderResponseError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)


RPC_STATUSES=frozenset({'INVALID_ARGUMENT','FAILED_PRECONDITION','PERMISSION_DENIED','UNAUTHENTICATED',
    'NOT_FOUND','RESOURCE_EXHAUSTED','INTERNAL','UNAVAILABLE','DEADLINE_EXCEEDED','UNIMPLEMENTED',
    'OUT_OF_RANGE','UNKNOWN','ABORTED','CANCELLED','ALREADY_EXISTS'})
RPC_REASONS=frozenset({'API_KEY_INVALID','API_KEY_EXPIRED','API_KEY_NOT_FOUND','API_KEY_REVOKED',
    'API_KEY_SERVICE_BLOCKED','API_KEY_HTTP_REFERRER_BLOCKED','API_KEY_IP_ADDRESS_BLOCKED',
    'API_KEY_ANDROID_APP_BLOCKED','API_KEY_IOS_APP_BLOCKED','SERVICE_DISABLED','BILLING_DISABLED',
    'CONSUMER_INVALID','ACCESS_TOKEN_SCOPE_INSUFFICIENT','ACCESS_TOKEN_TYPE_UNSUPPORTED'})
RPC_FIELDS=frozenset({'model','contents','contents[].role','contents[].parts','contents[].parts[].text',
    'systemInstruction','systemInstruction.parts','systemInstruction.parts[].text','generationConfig',
    'generationConfig.maxOutputTokens','generationConfig.responseMimeType','generationConfig.responseJsonSchema',
    'generationConfig.candidateCount','generationConfig.temperature','generationConfig.topP','generationConfig.topK'})


class ProviderHTTPDetails(Model):
    http_status: int=Field(ge=400,le=599)
    status: str|None=None
    reasons: list[str]=Field(default_factory=list,max_length=16)
    fields: list[str]=Field(default_factory=list,max_length=16)

    @model_validator(mode='after')
    def check_allowlist(self):
        if self.status is not None and self.status not in RPC_STATUSES:raise ValueError('Unknown provider status')
        if any(value not in RPC_REASONS for value in self.reasons):raise ValueError('Unknown provider reason')
        if any(value not in RPC_FIELDS for value in self.fields):raise ValueError('Unknown provider field')
        return self


class GeminiHTTPError(RuntimeError):
    """Only fixed vocabulary survives; no message, metadata, localized text or raw body."""
    def __init__(self,details):
        self.details=details
        super().__init__('HTTP status '+str(details.http_status))

    @classmethod
    def from_response(cls,http_status,body):
        from agentguard.integrations.http import _json
        summary=ProviderHTTPDetails(http_status=http_status)
        try:
            parsed=_json(body)
            error=parsed.get('error') if isinstance(parsed,dict) else None
            if not isinstance(error,dict):return cls(summary)
            if 'code' in error and (type(error['code']) is not int or error['code']!=http_status):return cls(summary)
            status=error.get('status')
            if isinstance(status,str) and status in RPC_STATUSES:summary.status=status
            details=error.get('details',[])
            for item in (details[:16] if isinstance(details,list) else []):
                if not isinstance(item,dict):continue
                if item.get('@type')=='type.googleapis.com/google.rpc.ErrorInfo':
                    reason=item.get('reason')
                    if isinstance(reason,str) and reason in RPC_REASONS and reason not in summary.reasons:summary.reasons.append(reason)
                elif item.get('@type')=='type.googleapis.com/google.rpc.BadRequest':
                    violations=item.get('fieldViolations',[])
                    for violation in (violations[:16] if isinstance(violations,list) else []):
                        field=violation.get('field') if isinstance(violation,dict) else None
                        if not isinstance(field,str) or len(field)>256:continue
                        field=re.sub(r'\[[0-9]{1,3}\]','[]',field)
                        # protobuf field paths may use snake_case; map only known identifiers.
                        for snake,camel in {'system_instruction':'systemInstruction','generation_config':'generationConfig',
                            'max_output_tokens':'maxOutputTokens','response_mime_type':'responseMimeType',
                            'response_json_schema':'responseJsonSchema','candidate_count':'candidateCount',
                            'top_p':'topP','top_k':'topK'}.items():
                            field='.'.join(camel if part==snake else part for part in field.split('.'))
                        if field in RPC_FIELDS and field not in summary.fields and len(summary.fields)<16:summary.fields.append(field)
        except Exception:
            # Malformed/non-JSON errors still preserve HTTP status; never echo parsing errors.
            return cls(ProviderHTTPDetails(http_status=http_status))
        return cls(summary)


class GeminiProvider:
    """Gemini REST generateContent text-only adapter; hidden thinking is charged too."""
    def generate(self,config,messages,max_output_tokens,*,secret,cancel_event):
        from agentguard.integrations.http import Endpoint
        if not secret:raise ProviderResponseError('CREDENTIAL_UNAVAILABLE')
        # Gemini 3+ migration guide requires omitting candidate_count; newer models
        # also deprecate sampling controls. Use the server's single-candidate default
        # and retain strict response cardinality/usage checks below. No retry/fallback.
        body={'contents':[{'role':'model' if row['role']=='assistant' else 'user','parts':[{'text':row['content']}]} for row in messages],
              'generationConfig':{'maxOutputTokens':max_output_tokens}}
        if config.system_prompt:body['systemInstruction']={'parts':[{'text':config.system_prompt}]}
        if config.response_format=='json':body['generationConfig']['responseMimeType']='application/json'
        data,_,_=request_json(Endpoint('https://generativelanguage.googleapis.com',timeout_seconds=config.timeout_seconds),
            'POST','/v1beta/models/'+config.model+':generateContent',body,headers={'x-goog-api-key':secret},cancel_event=cancel_event,
            accepted=(200,),error_factory=GeminiHTTPError.from_response)
        candidates=data.get('candidates',[])
        if len(candidates)==1 and candidates[0].get('finishReason')=='MAX_TOKENS':raise ProviderResponseError('PROVIDER_MAX_TOKENS')
        if data.get('promptFeedback',{}).get('blockReason') or len(candidates)!=1 or candidates[0].get('finishReason')!='STOP':raise ProviderResponseError('PROVIDER_BLOCKED_OR_INCOMPLETE')
        parts=candidates[0].get('content',{}).get('parts',[])
        if not parts or any(set(part)-{'text','thought','thoughtSignature'} or not isinstance(part.get('text'),str) for part in parts):raise ValueError('Unsupported non-text provider response')
        text=''.join(part['text'] for part in parts if not part.get('thought'))
        usage=data.get('usageMetadata')
        if not isinstance(usage,dict) or not {'promptTokenCount','totalTokenCount'}<=usage.keys():raise ProviderResponseError('PROVIDER_USAGE_MISSING')
        prompt=usage['promptTokenCount'];total=usage['totalTokenCount'];response=usage.get('candidatesTokenCount',0);thoughts=usage.get('thoughtsTokenCount',0)
        if any(type(value) is not int or value<0 for value in (prompt,total,response,thoughts)) or total!=prompt+response+thoughts or usage.get('toolUsePromptTokenCount',0):raise ProviderResponseError('PROVIDER_USAGE_INCONSISTENT')
        return ProviderResult(text.replace(secret,'[REDACTED]'),prompt,total-prompt,data['modelVersion'].replace(secret,'[REDACTED]'))


class JSONHTTPProvider:
    """Versioned text bridge for local providers/contract testing, not an LLM claim."""
    def generate(self,config,messages,max_output_tokens,*,secret,cancel_event):
        body=dict(protocol_version=1,model=config.model,messages=messages,system_prompt=config.system_prompt,max_output_tokens=max_output_tokens)
        endpoint=config.endpoint.endpoint()
        endpoint=replace(endpoint,timeout_seconds=min(endpoint.timeout_seconds,config.timeout_seconds))
        data,_,_=request_json(endpoint,'POST','/',body,cancel_event=cancel_event)
        if not isinstance(data,dict) or set(data)!={'protocol_version','text','input_tokens','output_tokens','model_version'} or type(data['protocol_version']) is not int or data['protocol_version']!=1:raise ValueError('Invalid model bridge response')
        return ProviderResult(data['text'],data['input_tokens'],data['output_tokens'],data['model_version'])


class MockProvider:
    def generate(self,config,messages,max_output_tokens,*,secret,cancel_event):return ProviderResult(config.mock_output,0,0,'mock-v1')


class ModelCall(Model):
    sequence: int
    purpose: Literal['agent','judge','generation']
    prompt_digest: str
    status: Literal['completed','error','blocked']
    usage_source: Literal['provider-reported','mock','unknown']
    input_tokens: int=Field(default=0,ge=0)
    output_tokens: int=Field(default=0,ge=0)
    reserved_tokens: int=Field(ge=0)
    reserved_cost_micro_usd: int=Field(ge=0)
    estimated_cost_micro_usd: int=Field(default=0,ge=0)
    model_version: str=''
    error_code: str|None=None
    provider_error: ProviderHTTPDetails|None=None

    @model_serializer(mode='wrap')
    def serialize(self,handler):
        data=handler(self)
        if self.provider_error is None:data.pop('provider_error',None)
        return data


class GatewayEvidence(Model):
    config: GatewayConfig
    mode: Literal['live','mock']
    status: Literal['passed','blocked','unverified']
    calls: list[ModelCall]
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        if self.mode!=self.config.mode:raise ValueError('Usage mode mismatch')
        if [row.sequence for row in self.calls]!=list(range(1,len(self.calls)+1)):raise ValueError('Usage sequence mismatch')
        if self.status=='passed' and any(row.status!='completed' or row.usage_source!=('mock' if self.mode=='mock' else 'provider-reported') for row in self.calls):raise ValueError('Unsettled usage cannot pass')
        if self.status=='passed' and (len(self.calls)>self.config.max_calls or sum(row.input_tokens+row.output_tokens for row in self.calls)>self.config.max_total_tokens or sum(row.estimated_cost_micro_usd for row in self.calls)>self.config.max_cost_micro_usd):raise ValueError('Usage budget exceeded')
        for row in self.calls:
            if row.provider_error is not None:
                details=ProviderHTTPDetails.model_validate(row.provider_error.model_dump())
                if row.status!='error' or row.usage_source!='unknown' or row.error_code!='HTTP_'+str(details.http_status):raise ValueError('HTTP error contradicts usage status')
            expected_cost=(row.input_tokens*self.config.input_rate_micro_usd_per_million+row.output_tokens*self.config.output_rate_micro_usd_per_million+999999)//1000000
            if row.status=='completed' and (not row.model_version or row.input_tokens+row.output_tokens>row.reserved_tokens or row.estimated_cost_micro_usd!=expected_cost or row.estimated_cost_micro_usd>row.reserved_cost_micro_usd):raise ValueError('Usage/pricing reservation mismatch')
        expected=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if self.fingerprint is not None and expected!=self.fingerprint:raise ValueError('Usage evidence changed')
        self.fingerprint=expected;return self


class ModelGateway:
    def __init__(self,config,*,providers=None,cancel_event=None):
        self.config=GatewayConfig.model_validate(config.model_dump(mode='json'))
        registry={'gemini':GeminiProvider(),'json-http':JSONHTTPProvider(),'mock':MockProvider()}
        registry.update(providers or {}) # Explicit trusted SDK adapters, never dynamic imports.
        if self.config.provider not in registry:raise ValueError('Unsupported model provider')
        self.provider=registry[self.config.provider];self.cancel=cancel_event
        self._lock=threading.Lock();self._active=False;self._last=0.;self._calls=[];self._status='passed'
    def _cost(self,input_tokens,output_tokens):
        return (input_tokens*self.config.input_rate_micro_usd_per_million+output_tokens*self.config.output_rate_micro_usd_per_million+999999)//1000000
    def generate(self,messages,*,max_output_tokens=128,purpose='agent',remaining_seconds=None):
        args=ModelArguments.model_validate(dict(messages=messages,max_output_tokens=max_output_tokens))
        if purpose not in {'agent','judge','generation'}:raise ValueError('Invalid usage purpose')
        rows=[row.model_dump() for row in args.messages]
        prompt=dict(messages=rows,system=self.config.system_prompt,version=self.config.prompt_version)
        # UTF-8 byte reservation plus framing margin is conservative, not a tokenizer measurement.
        reserved=len(canonical_json(prompt).encode())+256+args.max_output_tokens
        cost=self._cost(reserved-args.max_output_tokens,args.max_output_tokens)
        with self._lock:
            call=ModelCall(sequence=len(self._calls)+1,purpose=purpose,prompt_digest=digest(prompt),status='blocked',usage_source='unknown',reserved_tokens=reserved,reserved_cost_micro_usd=cost)
            used_tokens=sum(row.input_tokens+row.output_tokens if row.status=='completed' else row.reserved_tokens for row in self._calls)
            used_cost=sum(row.estimated_cost_micro_usd if row.status=='completed' else row.reserved_cost_micro_usd for row in self._calls)
            denied=(self._status!='passed' or self._active or len(self._calls)>=self.config.max_calls or args.max_output_tokens>self.config.max_output_tokens or len(canonical_json(prompt))>self.config.max_input_chars or used_tokens+reserved>self.config.max_total_tokens or used_cost+cost>self.config.max_cost_micro_usd or self._last and time.monotonic()-self._last<self.config.min_call_interval_seconds)
            self._calls.append(call)
            if denied:
                call.error_code='BUDGET_OR_RATE_DENIED';self._status='blocked';raise RuntimeError('Model budget, concurrency or rate policy denied the call')
            self._active=True;self._last=time.monotonic()
        try:
            if self.cancel is not None and self.cancel.is_set():raise RuntimeError('Model call cancelled')
            config=self.config.model_copy(deep=True)
            if remaining_seconds is not None:
                if remaining_seconds<=0:raise RuntimeError('Model deadline has elapsed')
                config.timeout_seconds=min(config.timeout_seconds,remaining_seconds)
            # Read exactly the declared variable, only when a Gemini request is actually attempted.
            secret=os.environ.get('GEMINI_API_KEY') if config.provider=='gemini' else None
            result=self.provider.generate(config,rows,args.max_output_tokens,secret=secret,cancel_event=self.cancel)
            if not isinstance(result.text,str) or not result.text or len(result.text.encode())>8192 or not isinstance(result.model_version,str) or not result.model_version or len(result.model_version)>200:raise ValueError('Invalid model output')
            if any(type(n) is not int or n<0 for n in (result.input_tokens,result.output_tokens)):raise ProviderResponseError('PROVIDER_USAGE_INCONSISTENT')
            if result.input_tokens>reserved-args.max_output_tokens or result.output_tokens>args.max_output_tokens:raise ProviderResponseError('PROVIDER_USAGE_RESERVATION_EXCEEDED')
            with self._lock:
                call.status='completed';call.usage_source='mock' if config.mode=='mock' else 'provider-reported'
                call.input_tokens=result.input_tokens;call.output_tokens=result.output_tokens
                call.estimated_cost_micro_usd=self._cost(result.input_tokens,result.output_tokens);call.model_version=result.model_version
            return result.text
        except Exception as exc:
            from agentguard.execution.errors import ExecutionTimeout,ExecutionCancelled
            code='MODEL_CALL_UNVERIFIED'
            if isinstance(exc,ProviderResponseError) and exc.code in {'CREDENTIAL_UNAVAILABLE','PROVIDER_MAX_TOKENS','PROVIDER_BLOCKED_OR_INCOMPLETE','PROVIDER_USAGE_MISSING','PROVIDER_USAGE_INCONSISTENT','PROVIDER_USAGE_RESERVATION_EXCEEDED'}:code=exc.code
            elif isinstance(exc,ExecutionTimeout):code='MODEL_TIMEOUT'
            elif isinstance(exc,ExecutionCancelled):code='MODEL_CANCELLED'
            elif isinstance(exc,(ValueError,KeyError,TypeError)):code='MODEL_RESPONSE_INVALID'
            elif isinstance(exc,OSError):code='MODEL_TLS_OR_NETWORK_ERROR'
            elif isinstance(exc,RuntimeError) and re.fullmatch(r'HTTP status [1-5][0-9]{2}',str(exc)):code='HTTP_'+str(exc)[-3:]
            with self._lock:call.status='error';call.error_code=code;self._status='unverified'
            if isinstance(exc,GeminiHTTPError):
                with self._lock:call.provider_error=exc.details.model_copy(deep=True)
            # Provider/transport exceptions may contain credentials; never chain/log their text.
            raise RuntimeError('Model call failed ['+code+']; usage remains reserved and unverified') from None
        finally:
            with self._lock:self._active=False
    def evidence(self):
        with self._lock:return GatewayEvidence(config=self.config,mode=self.config.mode,status='unverified' if self._active else self._status,calls=deepcopy(self._calls))
