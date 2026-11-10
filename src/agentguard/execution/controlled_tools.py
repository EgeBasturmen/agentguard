"""Trusted simulator handlers. Domain choices stay outside the generic controller."""
from pydantic import BaseModel, ConfigDict, StrictStr
from agentguard.execution.tool_registry import ToolRegistry
from agentguard.execution.tool_broker import GetOrderArgs, RefundOrderArgs, DeleteCustomerArgs

class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: StrictStr
class WriteArgs(FileArgs):
    content: StrictStr

def read(state, filename): return state["files"][filename]
def write(state, filename, content): state["files"][filename] = content
def delete(state, filename): del state["files"][filename]
def get_order(state, order_id): return state["orders"][order_id]
def refund(state, order_id, amount):
    order = state["orders"][order_id]
    item = dict(id=f"REF-{len(state['refunds']) + 1}", order_id=order_id, amount=amount)
    state["refunds"].append(item)
    order["status"] = "refunded"
    return item
def delete_customer(state, customer_id): state["customers"][customer_id]["status"] = "deleted"

def registry_for(environment):
    registry = ToolRegistry()
    if environment == "files":
        rows = [("file.read", FileArgs, read), ("file.write", WriteArgs, write), ("file.delete", FileArgs, delete)]
    elif environment == "commerce":
        rows = [("order.get", GetOrderArgs, get_order), ("payment.refund", RefundOrderArgs, refund), ("customer.delete", DeleteCustomerArgs, delete_customer)]
    else: raise ValueError(f"Unsupported controlled environment: {environment}")
    for name, schema, handler in rows: registry.register(name, schema, handler, stateful=True)
    return registry, frozenset(row[0] for row in rows)
