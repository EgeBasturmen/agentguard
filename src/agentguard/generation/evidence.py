"""Release admission receipts: content binding, not signatures or proof of oracle truth."""
from pydantic import Field,model_validator
from agentguard.core.manifest import digest
from agentguard.generation.models import Model

class ApprovalEvidence(Model):
    schema_version: int=1
    case_id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    revision: int=Field(ge=1)
    candidate_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    scenario_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    comparison_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    suite_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    review_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    sources_fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Unsupported approval evidence version')
        actual=digest(self.model_dump(exclude={'fingerprint'}))
        if self.fingerprint is not None and self.fingerprint!=actual:raise ValueError('Approval receipt fingerprint mismatch')
        self.fingerprint=actual;return self
