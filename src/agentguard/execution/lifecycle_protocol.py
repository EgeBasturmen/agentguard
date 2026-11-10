from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LifecycleMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[2] = 2
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class StartRequest(LifecycleMessage):
    type: Literal["start"] = "start"
    reference: str = Field(max_length=300)
    task: str = Field(max_length=8192)


class Started(LifecycleMessage):
    type: Literal["started"] = "started"


class Completed(LifecycleMessage):
    type: Literal["completed"] = "completed"
    status: Literal["ok", "error"]
    output: str | None = Field(default=None, max_length=8192)
    error_type: str | None = Field(default=None, max_length=200)
    error_message: str | None = Field(default=None, max_length=1000)
