"""Agent adapters keep untrusted code off the host. HTTP is explicitly observed-only."""
from dataclasses import dataclass
from pydantic import BaseModel,ConfigDict,Field
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.integrations.http import Endpoint,request_json
from agentguard.contracts.compatibility import controlled_capabilities
from agentguard.contracts.models import Capabilities

class ContainerLaunchSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    image: str=Field(pattern=r'^(?:sha256:[0-9a-f]{64}|[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64})$')
    reference: str=Field(default='agent',min_length=1,max_length=300)

@dataclass(frozen=True)
class PythonAgent:
    launch: AgentLaunchSpec
    transport: str='python'
    def capabilities(self): return controlled_capabilities()

@dataclass(frozen=True)
class ContainerAgent:
    launch: ContainerLaunchSpec
    transport: str='container'
    def capabilities(self): return controlled_capabilities()

@dataclass(frozen=True)
class HTTPAgent:
    endpoint: Endpoint
    transport: str='http'
    def capabilities(self): return Capabilities(supports_output=True,supports_state_snapshot=True)
    def invoke(self,task,run_id,namespace,cancel_event=None):
        result,_,_=request_json(self.endpoint,'POST','/',dict(protocol_version=1,run_id=run_id,namespace=namespace,task=task),cancel_event=cancel_event)
        if not isinstance(result,dict) or set(result)!={'protocol_version','run_id','output'} or type(result['protocol_version']) is not int or result['protocol_version']!=1 or result['run_id']!=run_id:
            raise ValueError('Invalid HTTP agent result; state/trace/verdict claims are not accepted')
        output=result['output']
        if output is not None and (not isinstance(output,str) or len(output.encode())>8192): raise ValueError('HTTP agent output exceeds contract')
        return output
