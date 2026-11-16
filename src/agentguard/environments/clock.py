"""Explicit bounded virtual time; no wall-clock sleep, arbitrary callbacks or hidden effects."""
from dataclasses import dataclass,replace
import json
from agentguard.environments.adapters import DocumentEnvironment
from agentguard.environments.services import identity,contract,TEXT
from agentguard.core.manifest import canonical_json

@dataclass
class ClockEnvironment(DocumentEnvironment):
    kind: str='clock'
    def clone(self):return replace(self)
    def validate_fixture(self,fixture):self._check(fixture)
    def _check(self,state):
        if set(state)!={'clock','values','jobs','history'} or type(state['clock']) is not int or not 0<=state['clock']<=2**53:raise ValueError('Invalid virtual clock fixture')
        if not isinstance(state['values'],dict) or not isinstance(state['jobs'],dict) or not isinstance(state['history'],list):raise ValueError('Invalid job fixture')
        if len(state['jobs'])>100 or len(state['history'])>1000 or len(canonical_json(state).encode())>512*1024:raise ValueError('Virtual fixture bound exceeded')
        for key in state['values']:identity(key)
        for key,job in state['jobs'].items():
            identity(key)
            if not isinstance(job,dict) or set(job)!={'due','key','value'} or type(job['due']) is not int or not state['clock']<=job['due']<=2**53:raise ValueError('Invalid pending job')
            identity(job['key'])
        # History is authoritative only when produced during this run; initial fixture
        # history may be seeded but never presented as observed execution receipts.
    def reset(self,fixture):
        self._check(fixture);self._path('clock.json').write_text(canonical_json(fixture),encoding='utf-8')
    def snapshot(self):
        state=json.loads(self._path('clock.json').read_text(encoding='utf-8'));self._check(state);return state
    def invoke(self,name,arguments):
        state=self.snapshot()
        if name=='job.schedule':
            if set(arguments)!={'id','due','key','value'}:raise ValueError('Unexpected schedule fields')
            key=identity(arguments['id'])
            if key in state['jobs'] or any(row.get('id')==key for row in state['history']):raise ValueError('Duplicate job identity')
            state['jobs'][key]={k:v for k,v in arguments.items() if k!='id'}
        elif name=='job.cancel':
            if set(arguments)!={'id'}:raise ValueError('Unexpected cancel fields')
            key=identity(arguments['id'])
            if key not in state['jobs']:raise ValueError('Unknown pending job')
            del state['jobs'][key]
            state['history'].append(dict(id=key,status='cancelled',at=state['clock']))
        elif name=='clock.advance':
            if set(arguments)!={'to'} or type(arguments['to']) is not int or not state['clock']<=arguments['to']<=2**53:raise ValueError('Clock cannot move backwards or outside bounds')
            for key,job in sorted(list(state['jobs'].items()),key=lambda row:(row[1]['due'],row[0])):
                if job['due']>arguments['to']:continue
                state['values'][job['key']]=job['value'];del state['jobs'][key]
                state['history'].append(dict(id=key,status='completed',at=job['due']))
            state['clock']=arguments['to']
        else:raise PermissionError('Unknown virtual-clock operation')
        self._check(state)
        target=self._path('clock.json');temporary=self._path('next.json');temporary.write_text(canonical_json(state),encoding='utf-8');temporary.replace(target)
        return {'clock':state['clock'],'pending':len(state['jobs'])}
    def observation(self):
        state=self.snapshot();return dict(observed_until=state['clock'],pending=sorted(state['jobs']),external_delayed_effects_observable=False)
    def inspect_completion(self):
        seen=self.observation()
        return dict(complete=not seen['pending'],coverage_gaps=['Only declared virtual jobs within the advanced observation window are observed; external delayed effects are not verified.'])
    def contracts(self):
        bound={'type':'integer','minimum':0,'maximum':2**53}
        return [contract('job.schedule',dict(id=TEXT,due=bound,key=TEXT,value={}),['id','due','key','value']),
            contract('job.cancel',dict(id=TEXT),['id']),contract('clock.advance',dict(to=bound),['to'])]
