"""Standard-library agent source, imported only by the isolated worker."""
class FileAgent:
    async def run(self, task, sandbox):
        sandbox.call('file.write', {'filename':'note.txt','content':'done'})
        return 'note.txt updated'
