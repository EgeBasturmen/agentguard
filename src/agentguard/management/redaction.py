"""Presentation redaction never edits executable fixtures/oracles behind the user's back."""
import re
from agentguard.regression.repro import SECRET
from agentguard.management.store import AccessError

SENSITIVE={'apikey','password','secret','authorization','token','accesstoken','refreshtoken','credential'}
MANAGEMENT_SECRET=re.compile(r'ag7(?:r|e)?_[A-Za-z0-9_-]{35,}|AKIA[0-9A-Z]{16}')
def normalized(key):return re.sub('[^a-z0-9]','',key.lower())

def redact(value,fields=()):
    fields={normalized(name) for name in fields}|SENSITIVE
    hidden=set()
    def strings(item):
        if isinstance(item,str) and item:hidden.add(item)
        elif isinstance(item,dict):
            for child in item.values():strings(child)
        elif isinstance(item,list):
            for child in item:strings(child)
    def collect(item):
        if isinstance(item,dict):
            for key,child in item.items():
                if normalized(key) in fields:strings(child)
                else:collect(child)
        elif isinstance(item,list):
            for child in item:collect(child)
    collect(value)
    if len(hidden)>1000:raise AccessError('REDACTION_LIMIT',413)
    def walk(item):
        if isinstance(item,dict):return {key:'[REDACTED]' if normalized(key) in fields else walk(child) for key,child in item.items()}
        if isinstance(item,list):return [walk(child) for child in item]
        if isinstance(item,str):
            output=MANAGEMENT_SECRET.sub('[REDACTED]',SECRET.sub('[REDACTED]',item))
            for secret in sorted(hidden,key=len,reverse=True):output=output.replace(secret,'[REDACTED]')
            return output
        return item
    return walk(value)

def reject_credentials(value):
    def check(item,parent=''):
        if isinstance(item,str):
            if SECRET.search(item) or MANAGEMENT_SECRET.search(item):raise AccessError('CREDENTIAL_CONTENT_REJECTED_USE_RUNNER_LOCAL_REFERENCES',400)
        elif isinstance(item,dict):
            for key,child in item.items():
                if parent!='properties' and normalized(key) in SENSITIVE and isinstance(child,str) and child and child!='[REDACTED]':raise AccessError('CREDENTIAL_CONTENT_REJECTED_USE_RUNNER_LOCAL_REFERENCES',400)
                check(child,key)
        elif isinstance(item,list):
            for child in item:check(child,parent)
    check(value)
