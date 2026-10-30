
from agentguard.execution.ipc_protocol import (
    IPCProtocolError,
    ToolCallRequest,
    ToolCallResponse,
    decode_ipc_message,
    encode_ipc_message,
)
from agentguard.execution.tool_broker import (
    ToolArgumentsError,
    ToolBroker,
    ToolCallLimitExceeded,
    ToolNotAllowedError,
)


class IPCDispatcher:
    """
    Trusted controller-side dispatcher.

    Validates worker tool calls and forwards them
    to the trusted ToolBroker.
    """

    def __init__(self, broker: ToolBroker) -> None:
        self._broker = broker
        self._seen_call_ids: set[str] = set()

    def dispatch(self, data: bytes) -> bytes:
        # Reject malformed or oversized messages.
        request = decode_ipc_message(
            data,
            ToolCallRequest,
        )

        if request.call_id in self._seen_call_ids:
            raise IPCProtocolError(
                "Duplicate tool call ID."
            )

        self._seen_call_ids.add(request.call_id)

        try:
            result = self._broker.execute(
                request.tool_name,
                request.arguments,
            )

            response = ToolCallResponse(
                call_id=request.call_id,
                success=True,
                result=result,
            )

        except ToolNotAllowedError:
            response = ToolCallResponse(
                call_id=request.call_id,
                success=False,
                error_code="TOOL_NOT_ALLOWED",
            )

        except ToolArgumentsError:
            response = ToolCallResponse(
                call_id=request.call_id,
                success=False,
                error_code="INVALID_ARGUMENTS",
            )

        except ToolCallLimitExceeded:
            response = ToolCallResponse(
                call_id=request.call_id,
                success=False,
                error_code="TOOL_CALL_LIMIT_EXCEEDED",
            )

        except Exception:
            # Do not expose internal exceptions to the worker.
            response = ToolCallResponse(
                call_id=request.call_id,
                success=False,
                error_code="TOOL_EXECUTION_ERROR",
            )

        return encode_ipc_message(response)
