"""Phase 0 versioned definitions. Definitions describe requirements, not enforcement."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from agentguard.core.manifest import canonical_json
from agentguard.core.paths import values_equal
from agentguard.core.scenario import Scenario

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Version = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")]
CredentialRef = Annotated[str, StringConstraints(pattern=r"^(secret|env|credential)://[^\s]+$")]
CapabilityName = Literal[
    "supports_tool_observation", "supports_tool_interception", "supports_state_snapshot",
    "supports_reset", "supports_streaming", "supports_cancellation", "supports_model_usage",
    "supports_output", "supports_isolation", "supports_authoritative_evidence",
    "supports_sessions", "supports_memory_reset",
]
ExecutionMode = Literal["observed", "controlled"]
ToolMode = Literal["simulator", "staging", "replay", "mock", "custom"]
EnvironmentKind = Literal["filesystem", "http", "dataset", "database", "inbox", "browser", "message_bus", "custom"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VersionedContract(ContractModel):
    contract_version: Literal[1] = 1

    @field_validator("contract_version", mode="before")
    @classmethod
    def validate_contract_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Only integer contract_version=1 is supported.")
        return value


class Capabilities(ContractModel):
    # Absence means unsupported, never inferred from a filename or adapter name.
    supports_tool_observation: bool = False
    supports_tool_interception: bool = False
    supports_state_snapshot: bool = False
    supports_reset: bool = False
    supports_streaming: bool = False
    supports_cancellation: bool = False
    supports_model_usage: bool = False
    supports_output: bool = False
    supports_isolation: bool = False
    supports_authoritative_evidence: bool = False
    supports_sessions: bool = False
    supports_memory_reset: bool = False

    def supported(self) -> list[str]:
        return sorted(name for name, enabled in self.model_dump().items() if enabled)


class AdapterIdentity(ContractModel):
    id: Text
    version: Version
    protocol_version: int = Field(default=1, ge=1)


class AgentSpec(VersionedContract):
    id: Text
    version: Version
    adapter: AdapterIdentity
    transport: Literal["python", "http", "container", "custom"]
    entrypoint: Text
    execution_mode: ExecutionMode = "observed"
    credential_refs: list[CredentialRef] = Field(default_factory=list)
    # Endpoint/entrypoint is data here; validation must never import or contact it.


class ToolDefinition(VersionedContract):
    id: Text
    version: Version
    name: Text
    description: Text
    adapter: AdapterIdentity
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    effect_type: Literal["read", "write", "execute", "mixed", "unknown"] = "unknown"
    resource_scopes: list[Text] = Field(default_factory=list)
    test_mode: ToolMode
    timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    idempotency: Literal["yes", "no", "unknown"] = "unknown"
    credential_ref: CredentialRef | None = None

    @model_validator(mode="after")
    def validate_schema_documents(self) -> "ToolDefinition":
        if not self.input_schema:
            raise ValueError("A tool must declare a non-empty input schema.")
        canonical_json(self.input_schema)
        canonical_json(self.output_schema)
        return self


class EnvironmentSpec(VersionedContract):
    id: Text
    version: Version
    adapter: AdapterIdentity
    kind: EnvironmentKind
    test_mode: ToolMode
    fixture: dict[str, Any]
    namespace: Text | None = None

    @model_validator(mode="after")
    def validate_fixture(self) -> "EnvironmentSpec":
        canonical_json(self.fixture)
        return self


class SourceReference(ContractModel):
    kind: Literal["manual", "dataset", "incident", "generation"]
    uri: Text
    version: Text


class RuleReference(ContractModel):
    id: Text
    version: Version


class Message(ContractModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ExecutionLimits(ContractModel):
    timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    max_tool_calls: int = Field(default=100, ge=1)
    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ScenarioVersion(VersionedContract):
    id: Text
    version: Version
    provenance: list[SourceReference] = Field(min_length=1)
    agent: AgentSpec
    environment: EnvironmentSpec
    tools: list[ToolDefinition] = Field(default_factory=list)
    scenario: Scenario
    actor_id: Text = "test-actor"
    session_id: Text | None = None
    permissions: list[Text] = Field(default_factory=list)
    rules: list[RuleReference] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    required_capabilities: list[CapabilityName] = Field(default_factory=list)
    evaluator: AdapterIdentity
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    tags: list[Text] = Field(default_factory=list)
    severity: Literal["info", "warning", "error", "critical"] = "error"
    # Lifecycle state is declarative; review/approval transitions are AG-043.
    lifecycle: Literal["draft", "validated", "approved", "active", "retired"] = "draft"

    @model_validator(mode="after")
    def validate_references(self) -> "ScenarioVersion":
        if not values_equal(self.scenario.initial_state, self.environment.fixture):
            raise ValueError("Scenario initial_state must match its environment fixture.")
        for values in ([tool.id for tool in self.tools], [tool.name for tool in self.tools],
                       self.required_capabilities, self.permissions,
                       [rule.id for rule in self.rules]):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate tool, rule, permission or capability reference.")
        names = {tool.name for tool in self.tools}
        if any(check.tool_name not in names for check in self.scenario.tool_expectations):
            raise ValueError("Tool expectations must reference a declared tool.")
        if not set(self.permissions).issubset(names):
            raise ValueError("Permissions must reference declared tool names.")
        canonical_json(self.scenario.model_dump())
        return self


class ExecutionContext(VersionedContract):
    execution_mode: ExecutionMode
    adapter: AdapterIdentity
    capabilities: Capabilities
    limitations: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_controlled_claim(self) -> "ExecutionContext":
        if self.execution_mode == "controlled" and not all((
            self.capabilities.supports_isolation,
            self.capabilities.supports_tool_interception,
            self.capabilities.supports_authoritative_evidence,
        )):
            raise ValueError("Controlled context requires isolation, tool interception and authoritative evidence.")
        return self
