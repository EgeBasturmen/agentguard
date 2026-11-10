"""Standard-library worker client. No controller state or policy is shipped here."""
import json
import uuid

class ToolClient:
    def __init__(self, source, sink):
        self._source, self._sink = source, sink
    def call(self, name, arguments):
        call_id = uuid.uuid4().hex
        data = json.dumps(dict(protocol_version=1, type="tool_call", call_id=call_id,
                               tool_name=name, arguments=arguments), allow_nan=False).encode()
        if len(data) > 16384:
            raise RuntimeError("Tool request too large")
        self._sink.write(data + b"\n"); self._sink.flush()
        line = self._source.readline(16386)
        if not line.endswith(b"\n") or len(line) > 16385:
            raise RuntimeError("Invalid tool response")
        response = json.loads(line)
        if response.get("call_id") != call_id or response.get("type") != "tool_result":
            raise RuntimeError("Mismatched tool response")
        if not response.get("success"):
            raise RuntimeError(response.get("error_code", "Tool failed"))
        return response.get("result")
    def call_tool(self, name, **arguments): return self.call(name, arguments)
    def get_order(self, order_id): return self.call("order.get", dict(order_id=order_id))
    def refund_order(self, order_id, amount): return self.call("payment.refund", dict(order_id=order_id, amount=amount))
    def delete_customer(self, customer_id): return self.call("customer.delete", dict(customer_id=customer_id))
    def read_file(self, filename): return self.call("file.read", dict(filename=filename))
    def write_file(self, filename, content): return self.call("file.write", dict(filename=filename, content=content))
    def delete_file(self, filename): return self.call("file.delete", dict(filename=filename))
