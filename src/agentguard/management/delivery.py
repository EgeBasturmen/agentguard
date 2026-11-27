"""Opt-in bounded finish delivery only. Never retries claim, enrollment or execution."""
from dataclasses import dataclass
import hashlib
import math
import random
import time
from agentguard.core.manifest import digest
from agentguard.management.cache import CacheError
from agentguard.management.client import RPCTransportError,RPCRemoteError
from agentguard.management.store import AccessError

@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int=3
    total_seconds: float=15
    request_timeout: float=3
    base_delay: float=.25
    max_delay: float=2
    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1<=self.max_attempts<=5:raise ValueError('Bounded delivery attempts required')
        for value,limit in ((self.total_seconds,60),(self.request_timeout,10),(self.base_delay,5),(self.max_delay,5)):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=limit:raise ValueError('Bounded delivery timing required')
        if self.base_delay>self.max_delay or self.request_timeout>self.total_seconds:raise ValueError('Invalid delivery timing')

class DeliveryError(AccessError):
    def __init__(self,code,attempts,category):
        self.attempts=attempts;self.category=category;self.delivery_state='pending_upload'
        super().__init__(code,409)

def retryable(error):
    if isinstance(error,RPCTransportError):return True
    return isinstance(error,RPCRemoteError) and error.status in {500,502,503,504} and error.code in {
        'SERVICE_UNAVAILABLE','SERVER_BUSY','UPSTREAM_UNAVAILABLE','GATEWAY_TIMEOUT',
        'REMOTE_OPERATION_FAILED','OPERATION_FAILED_NO_RESULT_VERIFIED'}

def deliver_pending(cache,client,project,job,*,policy,clock=time.monotonic,sleep=time.sleep,jitter=random.uniform):
    if not isinstance(policy,RecoveryPolicy):raise ValueError('Explicit recovery policy required')
    with cache.session():
        owner=digest(dict(server=client.url,credential_hash=hashlib.sha256(client.token.encode()).hexdigest()))
        if owner!=cache.owner:raise CacheError('CACHE_OWNER_MISMATCH')
        pinned=cache.delivery(project,job);report_hash=digest(pinned.report)
        started=clock();attempts=0;category='budget'
        while attempts<policy.max_attempts:
            if digest(dict(server=client.url,credential_hash=hashlib.sha256(client.token.encode()).hexdigest()))!=cache.owner:raise CacheError('CACHE_OWNER_MISMATCH')
            remaining=policy.total_seconds-(clock()-started)
            if remaining<=0:break
            current=cache.delivery(project,job)
            if current.execution!=pinned.execution or current.lease!=pinned.lease or digest(current.report)!=report_hash:raise CacheError('CACHE_BINDING_MISMATCH')
            remaining=policy.total_seconds-(clock()-started)
            if remaining<=0:break
            attempts+=1
            try:
                reply=client.request('/api/runner/finish',current.request_body(),timeout=min(policy.request_timeout,remaining),expected_owner=cache.owner)
            except AccessError as error:
                if not retryable(error):raise
                category='transport' if isinstance(error,RPCTransportError) else 'temporary_server'
                if attempts>=policy.max_attempts:break
                remaining=policy.total_seconds-(clock()-started)
                delay=min(policy.max_delay,policy.base_delay*2**(attempts-1))*jitter(.5,1.0)
                if not math.isfinite(delay) or not 0<delay<remaining:break
                sleep(delay);continue
            cache.acknowledge_delivery(current,reply)
            return reply
        raise DeliveryError('FINISH_DELIVERY_RETRY_EXHAUSTED',attempts,category)
