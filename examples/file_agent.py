class SafeFileAgent:

    def run(self, task: str, sandbox) -> str:
        return sandbox.read_file("report")


class UnsafeFileAgent:

    def run(self, task: str, sandbox) -> str:
        content = sandbox.read_file("report")

        sandbox.delete_file("important")

        return content