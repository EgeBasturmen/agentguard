"""Typed, self-consistent integration metadata bound to the scenario comparison fingerprint."""
import hashlib
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from agentguard.core.manifest import canonical_json

class IntegrationEvidence(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    schema_version: Literal[1]
    agent_transport: Literal['python','container','http']
    environment: dict
    tools: list[dict]
    modes: list[Literal['simulator','staging','replay','mock']]
    required_capabilities: list[str]
    fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def validate_fingerprint(self):
        body=self.model_dump(exclude={'fingerprint'})
        actual=hashlib.sha256(canonical_json(body).encode()).hexdigest()
        if actual!=self.fingerprint: raise ValueError('Integration manifest fingerprint mismatch')
        from agentguard.integrations.tools import ToolContract
        for row in self.tools: ToolContract.model_validate(row['contract']).fingerprint()
        modes=sorted({row['mode'] for row in self.tools}) if self.tools else ['staging' if self.agent_transport=='http' else 'simulator']
        if modes!=self.modes: raise ValueError('Integration route modes are inconsistent')
        return self
