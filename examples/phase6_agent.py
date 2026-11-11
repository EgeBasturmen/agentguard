"""Generic file agent used as the current candidate by the CI regression gate."""
class Agent:
    def run(self,task,sandbox):
        sandbox.call('file.write',{'filename':'note.txt','content':'done'})
        return 'done'
