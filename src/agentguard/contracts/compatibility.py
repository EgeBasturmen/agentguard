"""Small, explicit supported matrix. No discovery, imports or runtime provisioning."""

from typing import Literal

from pydantic import Field, computed_field

from agentguard.contracts.models import (
    AdapterIdentity, Capabilities, ContractModel, EnvironmentKind, ExecutionContext,
    ExecutionMode, ScenarioVersion, ToolMode,
)

LOCAL_ADAPTER = AdapterIdentity(id="python.local", version="1.0.0")


def local_capabilities() -> Capabilities:
    return Capabilities(supports_state_snapshot=True, supports_tool_observation=True)


def controlled_capabilities() -> Capabilities:
    return Capabilities(supports_state_snapshot=True, supports_tool_observation=True,
        supports_tool_interception=True, supports_isolation=True,
        supports_authoritative_evidence=True, supports_cancellation=True, supports_output=True)


def controlled_context(adapter_id="python.container") -> ExecutionContext:
    return ExecutionContext(execution_mode="controlled",
        adapter=AdapterIdentity(id=adapter_id, version="1.0.0", protocol_version=2),
        capabilities=controlled_capabilities(), limitations=[
            "Authoritative evidence covers only trusted broker simulator state and instrumented tool calls.",
            "Docker shares the host kernel; this is not a VM or a hostile multi-tenant boundary.",
            ("Only explicitly included Python source and standard-library dependencies are supported."
             if adapter_id == "python.container" else "Pinned prebuilt container image: source/dependency provenance is not verified."),
            "Worker completion/output are untrusted claims; verdicts are computed by the controller.",
        ])


def local_context() -> ExecutionContext:
    return ExecutionContext(
        execution_mode="observed", adapter=LOCAL_ADAPTER.model_copy(deep=True),
        capabilities=local_capabilities(), limitations=[
            "Trusted local execution: agent code shares the controller process.",
            "State snapshots and instrumented tool traces are not authoritative against hostile code.",
            "Network, host access, deadlines and model budgets are not enforced by this runner.",
            "Streaming, cancellation, session reset, model usage and direct output evaluation are unsupported.",
        ],
    )


class AdapterSupport(ContractModel):
    adapter: AdapterIdentity
    component: Literal["core", "agent", "tool", "environment", "evaluator"]
    implementation: Literal["implemented", "contract_only"]
    execution_modes: list[ExecutionMode] = Field(default_factory=list)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    environment_kinds: list[EnvironmentKind] = Field(default_factory=list)
    tool_modes: list[ToolMode] = Field(default_factory=list)
    supported_protocols: list[str] = Field(default_factory=list)


def supported_matrix() -> list[AdapterSupport]:
    # Fresh objects prevent callers from altering the catalog for later runs.
    result = []
    for component, name in (
        ("core", "scenario.core"), ("agent", "python.local"),
        ("environment", "generic.memory"), ("tool", "generic.function"),
        ("evaluator", "builtin.state"),
    ):
        result.append(AdapterSupport(
            adapter=AdapterIdentity(id=name, version="1.0.0"), component=component,
            implementation="implemented", execution_modes=["observed"],
            capabilities=local_capabilities(),
            environment_kinds=["filesystem", "http", "dataset", "custom"],
            tool_modes=["simulator"],
        ))
    for component, name in (("agent", "http.agent"), ("agent", "container.agent"),
                            ("tool", "openapi.tool"), ("tool", "mcp.tool")):
        result.append(AdapterSupport(
            adapter=AdapterIdentity(id=name, version="0.0.0"),
            component=component, implementation="contract_only",
        ))
    result.append(AdapterSupport(
        adapter=AdapterIdentity(id="python.container", version="1.0.0", protocol_version=2),
        component="agent", implementation="implemented", execution_modes=["controlled"],
        capabilities=controlled_capabilities(), environment_kinds=["custom"], tool_modes=["simulator"],
    ))
    result.append(AdapterSupport(
        adapter=AdapterIdentity(id="container.ipc",version="1.0.0",protocol_version=2),component="agent",
        implementation="implemented",execution_modes=["controlled"],capabilities=controlled_capabilities(),
        environment_kinds=["custom"],tool_modes=["simulator","staging","replay","mock"],
    ))
    result.append(AdapterSupport(
        adapter=AdapterIdentity(id="http.endpoint",version="1.0.0"),component="agent",
        implementation="implemented",execution_modes=["observed"],
        capabilities=Capabilities(supports_output=True,supports_state_snapshot=True),
        environment_kinds=["http"],tool_modes=["staging"],
    ))
    for name,version,protocol in (("openapi.tool","3.1.1","3.1.1"),("mcp.tool","1.0.0","2025-11-25")):
        result.append(AdapterSupport(adapter=AdapterIdentity(id=name,version=version),component="tool",implementation="implemented",
            execution_modes=["controlled"],capabilities=Capabilities(supports_tool_observation=True,supports_tool_interception=True),
            tool_modes=["staging","replay","mock"],supported_protocols=[protocol]))
    for name,kind in (("filesystem.namespace","filesystem"),("document.dataset","dataset"),("http.namespace","http")):
        result.append(AdapterSupport(adapter=AdapterIdentity(id=name,version="1.0.0"),component="environment",implementation="implemented",
            execution_modes=["observed","controlled"],capabilities=Capabilities(supports_state_snapshot=True,supports_reset=True),
            environment_kinds=[kind],tool_modes=["staging"] if kind=="http" else ["simulator"],supported_protocols=["1"]))
    return result


class CompatibilityResult(ContractModel):
    capabilities: Capabilities
    missing_capabilities: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def supported(self) -> bool:
        return not self.issues and not self.missing_capabilities


def assess_compatibility(definition: ScenarioVersion) -> CompatibilityResult:
    definition = ScenarioVersion.model_validate(definition.model_dump())
    catalog = supported_matrix()
    requested = [
        ("core", AdapterIdentity(id="scenario.core", version="1.0.0")),
        ("agent", definition.agent.adapter),
        ("environment", definition.environment.adapter),
        ("evaluator", definition.evaluator),
        *(("tool", tool.adapter) for tool in definition.tools),
    ]
    issues = []
    selected = {}
    for component, identity in requested:
        match = next((row for row in catalog if row.component == component and row.adapter == identity), None)
        if match is None or match.implementation != "implemented":
            issues.append(f"Unsupported {component} adapter/version/protocol: {identity.id}@{identity.version}/{identity.protocol_version}")
        elif definition.agent.execution_mode not in match.execution_modes:
            issues.append(f"{identity.id} does not support {definition.agent.execution_mode} execution.")
        else:
            selected[component] = match
    available = local_capabilities() if not issues else Capabilities()
    if definition.agent.transport != "python":
        issues.append("The implemented agent adapter supports only trusted local Python objects.")
    environment = selected.get("environment")
    if environment is not None and (
        definition.environment.kind not in environment.environment_kinds
        or definition.environment.test_mode not in environment.tool_modes
    ):
        issues.append("Environment kind/test mode is not implemented by this adapter.")
    for tool in definition.tools:
        if tool.test_mode != "simulator":
            issues.append(f"Tool test mode is not implemented: {tool.name}/{tool.test_mode}")
    if definition.messages or definition.session_id is not None:
        issues.append("Multi-turn messages and session handling are not implemented.")
    if definition.rules:
        issues.append("Versioned rule resolution is not implemented; use explicit scenario checks.")
    if definition.limits.budget is not None:
        issues.append("Model budget enforcement is not implemented.")
    if definition.agent.credential_refs or any(tool.credential_ref for tool in definition.tools):
        issues.append("Credential resolution is not implemented by the local definition bridge.")
    missing = sorted(name for name in definition.required_capabilities if not getattr(available, name))
    limitations = list(local_context().limitations)
    if any(tool.effect_type == "unknown" for tool in definition.tools):
        limitations.append("Unknown tool effects are unverified, not declared safe.")
    limitations.append("Declared schemas, permissions, resource scopes and execution limits are not runtime enforcement in Phase 0.")
    return CompatibilityResult(
        capabilities=available, missing_capabilities=missing,
        issues=list(dict.fromkeys(issues)), limitations=limitations,
    )
