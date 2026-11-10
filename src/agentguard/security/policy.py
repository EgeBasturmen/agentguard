from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SecurityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_mode: Literal["local", "isolated"] = "isolated"

    allow_network: bool = False
    allow_host_filesystem: bool = False
    allow_host_environment: bool = False

    allowed_tools: frozenset[str] = Field(
        default_factory=frozenset,
    )

    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
    )

    memory_limit_mb: int = Field(
        default=256,
        ge=64,
        le=4096,
    )

    cpu_limit: float = Field(
        default=1.0,
        gt=0,
        le=4.0,
    )
    resource_scopes: dict[str, dict[str, frozenset[str]]] = Field(default_factory=dict)
    tool_timeout_seconds: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)
    max_tool_calls: int = Field(default=100, ge=1, le=10000, strict=True)
    max_stdout_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024, strict=True)
    max_stderr_bytes: int = Field(default=16 * 1024, ge=1024, le=1024 * 1024, strict=True)
