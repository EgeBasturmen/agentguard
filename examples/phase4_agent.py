"""Generic stdlib-only agent loaded inside the existing controlled Python worker."""


class WorkspaceAgent:
    async def run(self, task, sandbox):
        sandbox.call("file.write", {"filename": "note.txt", "content": "done"})
        return "done"
