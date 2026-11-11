import json

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from agentguard.core.mutation import StateMutation
from agentguard.core.trace import TraceEvent
from agentguard.core.manifest import ComparisonManifest
from agentguard.contracts.models import ExecutionContext
from agentguard.execution.records import AuditEvent, RuntimeManifest
from agentguard.evaluation.registry import EvaluationFinding



RunStatus= Literal["passed","failed","error"]
CleanupStatus = Literal[
    "not_required",
    "verified",
    "unverified",
]

class AssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    assertion_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    name: str
    passed: bool
    message: str | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    generation_ref: dict | None = None
    admission_status: Literal["external", "unapproved", "approved", "stale"] = "external"
    approval_evidence: dict | None = None
    evaluations: list[EvaluationFinding] = Field(default_factory=list)
    evaluation_evidence: dict = Field(default_factory=dict)
    model_evidence: dict | None = None
    turn_outputs: list[str|None] = Field(default_factory=list)
    integration_status: Literal["not_applicable", "verified", "unverified", "unsupported"] = "not_applicable"
    coverage_gaps: list[str] = Field(default_factory=list)
    integration_manifest: dict = Field(default_factory=dict)
    policy_status: Literal["unknown", "passed", "violation"] = "unknown"
    worker_output: str | None = Field(default=None, max_length=8192)
    run_manifest: RuntimeManifest | None = None
    audit: list[AuditEvent] = Field(default_factory=list)
    execution_context: ExecutionContext | None = None
    comparison_manifest: ComparisonManifest | None = None
    scenario_name: str

    passed: bool

    assertions: list[AssertionResult] = Field(
        default_factory=list
    )

    mutations: list[StateMutation] = Field(
        default_factory=list
    )

    traces: list[TraceEvent] = Field(
        default_factory=list
    )

    status: RunStatus = "passed"

    cleanup_status: CleanupStatus = "not_required"

    evaluation_status: Literal["not_run", "complete", "error"] = "not_run"
    evaluation_error_type: str | None = None
    evaluation_error_message: str | None = None
    error_stage: Literal[
        "validation", "provision", "snapshot", "execution", "diff", "evaluation"
    ] | None = None


    error_type: str | None = None
    error_message: str | None = None

    def validated_copy(self) -> "RunResult":
        return type(self).model_validate_json(
            json.dumps(self.model_dump(mode="json"), allow_nan=False)
        )
