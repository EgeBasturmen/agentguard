class SafeDocumentAgent:

    def run(self, task: str, sandbox) -> str:
        return sandbox.call_tool(
            "document.read",
            document_id="report",
        )


class UnsafeDocumentAgent:

    def run(self, task: str, sandbox) -> str:
        content = sandbox.call_tool(
            "document.read",
            document_id="report",
        )

        sandbox.call_tool(
            "document.delete",
            document_id="important",
        )

        return content