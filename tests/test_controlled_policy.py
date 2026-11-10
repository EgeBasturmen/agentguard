import time
import pytest
from pydantic import BaseModel,ConfigDict
from agentguard.core.scenario import ForbiddenChange
from agentguard.execution.tool_registry import ToolRegistry
from agentguard.execution.tool_broker import ToolBroker
from agentguard.security.policy import SecurityPolicy

class Args(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key: str
    value: str

def put(state,key,value):
    state[key]=value
    return value

def slow(state,key,value):
    time.sleep(2)
    state[key]=value
    return value


def make_broker(handler=put,**policy):
    registry=ToolRegistry()
    registry.register('kv.put',Args,handler,stateful=True)
    return ToolBroker(registry=registry,state={'safe':'old','protected':'keep'},
        policy=SecurityPolicy(allowed_tools=frozenset({'kv.put'}),**policy),
        forbidden_changes=[ForbiddenChange(path='protected')])


def test_policy_rejection_survives_caller_catching_exception():
    broker=make_broker(resource_scopes={'kv.put':{'key':frozenset({'safe'})}})
    with pytest.raises(Exception): broker.execute('kv.put',{'key':'protected','value':'bad'})
    assert broker.policy_violated
    assert broker.state['protected']=='keep'
    assert broker.audit[-1].reason=='RESOURCE_SCOPE_DENIED'


def test_tool_deadline_has_no_late_canonical_mutation():
    broker=make_broker(slow,tool_timeout_seconds=0.3)
    with pytest.raises(Exception): broker.execute('kv.put',{'key':'safe','value':'late'})
    time.sleep(0.1)
    assert broker.state['safe']=='old'
    assert broker.audit[-1].status=='error'


def test_forbidden_effect_rolls_back_and_records_attempt():
    broker = make_broker()
    with pytest.raises(Exception, match="FORBIDDEN_EFFECT"):
        broker.execute('kv.put', {'key':'protected','value':'broken'})
    assert broker.state == {'safe':'old','protected':'keep'}
    assert broker.audit[-1].mutations
    assert broker.audit[-1].reason == 'FORBIDDEN_EFFECT'
    assert broker.policy_violated


def test_successful_transaction_commits_independent_state():
    broker = make_broker()
    assert broker.execute('kv.put', {'key':'safe','value':'new'}) == 'new'
    copy = broker.state
    copy['safe'] = 'forged'
    assert broker.state['safe'] == 'new'
    assert broker.audit[-1].status == 'completed'


def test_cancellation_during_tool_has_no_late_commit():
    import threading
    from agentguard.execution.errors import ExecutionCancelled
    broker = make_broker(slow)
    event = threading.Event()
    broker._cancel = event
    timer = threading.Timer(0.2, event.set)
    timer.start()
    try:
        with pytest.raises(ExecutionCancelled): broker.execute('kv.put', {'key':'safe','value':'late'})
    finally: timer.cancel()
    assert broker.state['safe'] == 'old'
    assert broker.runtime_error is not None


def test_rejected_attempts_count_towards_call_limit():
    broker = make_broker(max_tool_calls=1)
    with pytest.raises(Exception): broker.execute('unknown', {})
    with pytest.raises(Exception): broker.execute('kv.put', {'key':'safe','value':'new'})
    assert [item.sequence for item in broker.audit] == [1,2]
    assert broker.audit[-1].reason == 'TOOL_CALL_LIMIT_EXCEEDED'
    assert broker.state['safe'] == 'old'


class NumericArgs(BaseModel):
    amount: float


def numeric_put(state, amount):
    state['safe'] = amount


@pytest.mark.parametrize('amount', [True, '1.0'])
def test_controlled_arguments_obey_numeric_schema_without_coercion(amount):
    from agentguard.execution.tool_broker import ToolArgumentsError
    registry = ToolRegistry()
    registry.register('number.put', NumericArgs, numeric_put, stateful=True)
    broker = ToolBroker(registry=registry, state={'safe':'old'},
        policy=SecurityPolicy(allowed_tools=frozenset({'number.put'})))
    with pytest.raises(ToolArgumentsError):
        broker.execute('number.put', {'amount':amount})
    assert broker.state['safe'] == 'old'
    assert broker.audit[-1].reason == 'INVALID_ARGUMENTS'
