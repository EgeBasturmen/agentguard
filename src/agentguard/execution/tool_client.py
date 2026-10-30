
import sys
from typing import Any, BinaryIO

from agentguard.execution.ipc_protocol import (
    IPCProtocolError,
    MAX_IPC_MESSAGE_BYTES,
    ToolCallRequest,
    ToolCallResponse,
    decode_ipc_message,
    encode_ipc_message,
)


class ToolClientError(RuntimeError):
    pass


class ToolClient:
    """
    Worker-side synchronous tool client.

    Sends tool requests to the trusted controller.
    Does not own canonical state.
    """

    def __init__(
        self,
        reader: BinaryIO | None = None,
        writer: BinaryIO | None = None,
    ) -> None:
        self._reader = reader if reader is not None else sys.stdin.buffer
        self._writer = writer if writer is not None else sys.stdout.buffer
        self._next_call_id = 1

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        call_id = f"call-{self._next_call_id}"
        self._next_call_id += 1

        request = ToolCallRequest(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )

        try:
            self._writer.write(encode_ipc_message(request))
            self._writer.flush()

            raw_response = self._reader.readline(
                MAX_IPC_MESSAGE_BYTES + 2
            )

            if not raw_response:
                raise ToolClientError(
                    "Controller closed IPC connection."
                )

            if not raw_response.endswith(b"\n"):
                raise ToolClientError(
                    "Invalid controller response framing."
                )

            response = decode_ipc_message(
                raw_response,
                ToolCallResponse,
            )

        except (OSError, ValueError, IPCProtocolError) as exc:
            raise ToolClientError(
                "Tool IPC communication failed."
            ) from exc

        if response.call_id != call_id:
            raise ToolClientError(
                "Controller response call ID mismatch."
            )

        if not response.success:
            raise ToolClientError(
                f"Tool call rejected: {response.error_code}"
            )

        return response.result

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self.call(
            "order.get",
            {"order_id": order_id},
        )

    def refund_order(
        self,
        order_id: str,
        amount: float,
    ) -> dict[str, Any]:
        return self.call(
            "payment.refund",
            {
                "order_id": order_id,
                "amount": amount,
            },
        )

    def delete_customer(self, customer_id: str) -> None:
        self.call(
            "customer.delete",
            {"customer_id": customer_id},
        )
