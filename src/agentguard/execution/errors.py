class ExecutionTimeout(RuntimeError):
    pass


class BuildTimeout(ExecutionTimeout):
    pass


class ExecutionCancelled(RuntimeError):
    pass


class ToolDeadlineError(ExecutionTimeout):
    pass

class ExecutionCleanupError(RuntimeError):
    """An external connector cannot verify its resource/session cleanup."""
