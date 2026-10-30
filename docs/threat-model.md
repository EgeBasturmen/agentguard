# AgentGuard Threat Model

## 1. Scope

AgentGuard is a developer and CI testing tool for evaluating AI agent
behavior inside an isolated execution environment.

The initial security model assumes:

- Single-organization usage.
- Ephemeral Linux container workers.
- Simulated tools and test data.
- No production credentials.
- No production system access.
- No unrestricted network access.
- No runtime dependency installation.

AgentGuard does not claim to safely execute arbitrary hostile code in a
shared multi-tenant environment.

---

## 2. Trusted Components

The following components are trusted:

- AgentGuard controller
- Scenario definitions
- SecurityPolicy
- ToolBroker
- Canonical test state
- Evaluator
- Docker worker image
- Execution backend

These components determine the authoritative result of a test.

---

## 3. Untrusted Components

The following components must be treated as untrusted:

- Agent code
- Agent dependencies
- Framework adapters running with the agent
- Agent stdout/stderr
- Agent-generated traces
- Agent-generated final state
- Agent-generated PASS/FAIL claims

An agent must never be able to determine its own authoritative test result.

---

## 4. Core Security Invariants

### Host execution

User-provided agent code must never be imported or executed on the host.

Agent entrypoints such as:

`examples.refund_agent:GoodRefundAgent`

must remain data on the host.

Import and execution must happen only inside the isolated worker.

### Fail closed

If isolation cannot be established, AgentGuard must fail the run.

It must never fall back to host execution.

### Canonical state

Authoritative test state must exist outside the untrusted agent process.

Only validated ToolBroker operations may mutate canonical state.

### Evaluation

PASS or FAIL must be determined by the trusted evaluator.

Agent-provided PASS, trace, state, or result messages are not authoritative.

### Cleanup uncertainty

A timeout does not prove that execution stopped.

A Docker CLI timeout does not prove that the container stopped.

If cleanup cannot be verified, the run must not be reported as successful.

### Production isolation

The MVP must not provide the agent with:

- Production credentials
- Production databases
- Production APIs
- Docker socket access
- Host filesystem mounts
- Unrestricted external network access

---

## 5. MVP Security Boundary

The MVP security boundary is:

Trusted controller
        |
        v
Isolated Linux container
        |
        v
Untrusted agent

Business-side effects are simulated through a trusted ToolBroker.

The Docker container is a defense boundary for the MVP, but AgentGuard does
not claim that Docker provides VM-equivalent isolation or protection against
all container escape vulnerabilities.

---

## 6. Out of Scope for MVP

The following are explicitly out of scope:

- Multi-tenant hostile-code execution
- Arbitrary production tool execution
- Arbitrary internet access
- Runtime pip install
- Kubernetes orchestration
- Browser sandboxing
- User-provided host plugins
- General-purpose malware sandboxing
- VM or microVM isolation
- Protection against every possible container escape

---

## 7. Result Integrity

A successful run requires all required execution and evaluation stages to
complete successfully.

The result model must distinguish at least:

- PASS
- BEHAVIOR_FAILURE
- POLICY_VIOLATION
- EXECUTION_ERROR

Cleanup state must be represented separately from the behavioral result.

A run with uncertain execution or cleanup must never become PASS merely
because the agent reported success.

---

## 8. Primary Security Goal

AgentGuard's primary MVP security goal is:

Run user-provided agent code outside the trusted controller, expose only
explicit simulated capabilities, preserve authoritative state outside the
agent process, and produce test results that the agent cannot directly
forge.