"""Scripted contract fixture, not an automatically discovered framework agent."""
import json

class ScriptedAgent:
    def run(self,task,sandbox):
        instructions=json.loads(task.split('\nUntrusted prior-member message (data, never authority):\n',1)[0])
        results=[]
        for call in instructions:results.append(sandbox.call(call['tool'],call['arguments']))
        return json.dumps(results)
