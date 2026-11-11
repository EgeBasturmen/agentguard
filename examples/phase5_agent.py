"""Provider-independent conversational agent; credentials never enter the worker."""

class AssistantAgent:
    def __init__(self):
        self.messages=[]

    async def run(self,task,sandbox):
        self.messages.append({'role':'user','content':task})
        output=sandbox.call('model.generate',{'messages':self.messages,'max_output_tokens':256})
        self.messages.append({'role':'assistant','content':output})
        return output
