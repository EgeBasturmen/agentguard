from typing import Literal

from pydantic import BaseModel, Field


class SecurityPolicy(BaseModel):
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