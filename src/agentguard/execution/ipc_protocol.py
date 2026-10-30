
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
)


IPC_PROTOCOL_VERSION = 1

MAX_IPC_MESSAGE_BYTES = 16 * 1024


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = IPC_PROTOCOL_VERSION
    type: Literal["tool_call"] = "tool_call"

    call_id: StrictStr = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    tool_name: StrictStr = Field(
        min_length=1,
        max_length=100,
    )

    arguments: dict[str, Any]


class ToolCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = IPC_PROTOCOL_VERSION
    type: Literal["tool_result"] = "tool_result"

    call_id: StrictStr = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    success: bool

    result: Any = None
    error_code: str | None = None


class IPCProtocolError(RuntimeError):
    pass


def encode_ipc_message(
    message: BaseModel,
) -> bytes:
    data = message.model_dump_json().encode("utf-8")

    if len(data) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError(
            "IPC message exceeds maximum allowed size."
        )

    return data + b"\n"


def decode_ipc_message(
    data: bytes,
    model_type: type[BaseModel],
) -> BaseModel:
    if len(data) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError(
            "IPC message exceeds maximum allowed size."
        )

    try:
        payload = json.loads(data)
        return model_type.model_validate(payload)

    except (ValueError, TypeError) as exc:
        raise IPCProtocolError(
            "Invalid IPC message."
        ) from exc
