"""Explicit custom-protocol adapter example. This trusted code runs in the broker, not the agent."""
from dataclasses import dataclass,replace
from urllib.parse import quote
from agentguard.sdk import Endpoint,ToolContract
from agentguard.integrations.http import request_json

@dataclass(frozen=True)
class ExistingJSONTool:
    endpoint: Endpoint
    def invoke(self,name,arguments):
        if name!='record.update': raise PermissionError('Unknown operation')
        resource=arguments['name']
        if not resource or any(c in resource for c in '/\\.%?#'): raise PermissionError('Unsafe resource')
        response,_,_=request_json(self.endpoint,'POST','/records/'+quote(resource,safe=''),{'value':arguments['value']})
        return response
    def bound(self,endpoint): return replace(self,endpoint=endpoint)
    def identity(self): return dict(adapter='custom.json@1.0.0',endpoint=self.endpoint.identity())
    @staticmethod
    def contract():
        return ToolContract(name='record.update',version='1.0.0',adapter='custom.json@1.0.0',effect_type='write',
            input_schema={'type':'object','properties':{'name':{'type':'string'},'value':{'type':'string'}},'required':['name','value'],'additionalProperties':False},
            output_schema={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False})
