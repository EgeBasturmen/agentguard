class ExecutionTimeout(RuntimeError):
    pass


class BuildTimeout(ExecutionTimeout):
    pass


class ExecutionCancelled(RuntimeError):
    pass


class ToolDeadlineError(ExecutionTimeout):
    pass
