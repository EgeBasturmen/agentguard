from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PROTOCOL_VERSION = 1

MAX_SCENARIO_NAME_LENGTH = 200
MAX_MESSAGE_LENGTH = 1000

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = PROTOCOL_VERSION

    scenario_name: str = Field(
        min_length=1,
        max_length=MAX_SCENARIO_NAME_LENGTH,
    )


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = PROTOCOL_VERSION

    status: Literal["ready"]

    scenario_name: str = Field(
        min_length=1,
        max_length=MAX_SCENARIO_NAME_LENGTH,
    )

    message: str = Field(
        max_length=MAX_MESSAGE_LENGTH,
    )