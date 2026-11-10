from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from agentguard.core.mutation import StateMutation


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: int
    tool_name: str
    arguments: dict[str, Any]
    status: Literal["completed", "denied", "error"]
    reason: str | None = None
    mutations: list[StateMutation] = Field(default_factory=list)


class RuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    protocol_version: Literal[2] = 2
    artifact_kind: Literal["python_source", "container_image"] = "python_source"
    image_id: str = ""
    base_digest: str = ""
    source_digest: str = ""
    worker_digest: str = ""
    dependencies: list[str] = Field(default_factory=list)
    container_id: str | None = None
    started: bool = False
    completed: bool = False
