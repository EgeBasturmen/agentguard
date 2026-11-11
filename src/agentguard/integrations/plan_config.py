"""Shared data-only endpoint config; no agent/controller/gateway import cycle."""
from pydantic import BaseModel,ConfigDict,Field
from agentguard.integrations.http import Endpoint

class EndpointConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    base_url: str
    allow_http: bool=False
    private_hosts: list[str]=Field(default_factory=list)
    timeout_seconds: float=10.0
    max_bytes: int=1024*1024
    def endpoint(self):return Endpoint(self.base_url,self.allow_http,frozenset(self.private_hosts),self.timeout_seconds,self.max_bytes)
